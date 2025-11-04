import os
import logging
import time
import regex  # Usamos 'regex' em vez de 're' para suportar \p{L}
import uvicorn
import asyncio
from fastapi import FastAPI, Request

from telegram import (
    Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, 
    ChatPermissions, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ChatMemberHandler, ContextTypes
)
from telegram.constants import ParseMode

# --- Configuração Inicial ---
# (Carregue estas variáveis de ambiente no seu servidor)
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Ex: "https://meu-servidor.com"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "impedirei") # Seu @ de suporte

# Configura o logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Função de Verificação (A Lógica Central) ---

async def check_user_profile(user_id: int, chat_id: int, bot: Bot) -> (bool, str):
    """
    Verifica o perfil do usuário (foto, username, nome) e retorna o status.
    Retorna (True, "") se for válido.
    Retorna (False, "motivo...") se for inválido.
    """
    reasons = []

    try:
        # 1. Obter os dados mais recentes do usuário no chat
        chat_member = await bot.get_chat_member(chat_id, user_id)
        user = chat_member.user

        # 2. Verificar Foto de Perfil
        profile_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if profile_photos.total_count == 0:
            reasons.append("sem foto de perfil (ou com ela privada)")

        # 3. Verificar @Username
        #if not user.username:
        #    reasons.append("sem nome de usuário")

        # 4. Verificar Nome (usando regex \p{L} para pegar QUALQUER letra)
        if not regex.search(r'\p{L}', user.first_name):
            reasons.append("nome inválido (apenas emojis ou símbolos)")
        
        # 5. Formatar o motivo
        if not reasons:
            return True, ""
        else:
            # Junta os motivos: "sem foto de perfil e sem nome de usuário"
            return False, " e ".join(reasons)

    except Exception as e:
        logger.error(f"Erro ao verificar perfil {user_id}: {e}")
        # Se o bot não conseguir verificar (ex: API offline), aprova por segurança.
        return True, ""


# --- Handler 1: Novo Membro no Grupo ---

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Disparado quando um novo membro entra no chat.
    """
    # Verifica se é uma atualização de um NOVO membro que ENTROU
    if not (update.chat_member and 
            update.chat_member.new_chat_member.status == ChatMember.MEMBER):
        return

    user = update.chat_member.new_chat_member.user
    chat = update.chat
    bot = context.bot

    logger.info(f"Novo membro {user.first_name} ({user.id}) no chat {chat.id}")

    # Roda a verificação de perfil
    is_valid, reason_text = await check_user_profile(user.id, chat.id, bot)

    if is_valid:
        logger.info(f"Usuário {user.id} aprovado na verificação.")
        # Opcional: Enviar boas-vindas simples
        # await bot.send_message(chat.id, f"Boas-vindas, {user.first_name}!")
        return

    # --- Se o perfil for INVÁLIDO ---
    logger.info(f"Usuário {user.id} REPROVADO: {reason_text}. Mutando por 1h.")

    # 1. Calcular o tempo de mute (1 hora = 3600 segundos)
    # (Exatamente como fizemos no MacroDroid, {system_time}+3600)
    until_date = int(time.time()) + 9999999

    # 2. Definir as permissões de MUTE
    permissions = ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False
    )

    # 3. Executar o Mute
    try:
        await bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user.id,
            permissions=permissions,
            until_date=until_date
        )
    except Exception as e:
        logger.error(f"Falha ao mutar {user.id}: {e}")
        # Se o bot não tiver permissão de admin, ele para aqui.
        return

    # 4. Construir os botões (como no MacroDroid)
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Já atualizei meu perfil", 
                callback_data="verify_profile"
            )
        ],
        [
            InlineKeyboardButton(
                "Suporte", 
                url=f"https://t.me/{ADMIN_USERNAME}"
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # 5. Montar e enviar a mensagem de aviso (com menção HTML)
    text = (
        f"Olá, <a href='tg://user?id={user.id}'>{user.first_name}</a>! Seja bem-vindo(a).\n\n"
        f"Detectamos que seu perfil está incompleto ({reason_text}).\n\n"
        "Por favor, atualize seu perfil e clique no botão abaixo para liberar seu acesso."
    )

    await bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

# --- Handler 2: Clique no Botão de Verificação ---

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Disparado quando o usuário clica em "verify_profile".
    """
    query = update.callback_query
    # O usuário que clicou no botão
    user = query.from_user 
    chat_id = query.message.chat.id
    bot = context.bot

    logger.info(f"Usuário {user.id} clicou no botão de verificação.")

    # 1. Re-verificar o perfil
    is_valid, reason = await check_user_profile(user.id, chat_id, bot)

    if is_valid:
        # --- APROVADO ---
        logger.info(f"Usuário {user.id} aprovado na RE-verificação. Desmutando.")

        # 2. Desmutar (dando permissões de volta)
        # (Passar 'None' para 'until_date' torna a permissão permanente)
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_invite_users=True, # Permitir que convide (opcional)
        )
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user.id,
            permissions=permissions
        )

        # 3. Enviar o Pop-up de Sucesso (answerCallbackQuery)
        await query.answer(
            "✅ Perfil verificado! Seu acesso foi liberado.", 
            show_alert=True
        )

        # 4. Apagar a mensagem de mute (deleteMessage)
        try:
            await query.message.delete()
        except Exception as e:
            logger.warn(f"Falha ao apagar mensagem {query.message.id}: {e}")

    else:
        # --- REPROVADO ---
        logger.info(f"Usuário {user.id} falhou na RE-verificação: {reason}")
        
        # 2. Enviar o Pop-up de Erro (answerCallbackQuery)
        # (Exatamente como o seu, texto puro, pois HTML não é suportado)
        await query.answer(
            f"Ops! Seu perfil ainda está incompleto. "
            f"Verifique se você adicionou uma foto PÚBLICA e um username, e tente novamente.\n\n"
            f"Em caso de engano, contate @{ADMIN_USERNAME}",
            show_alert=True
        )

# --- Configuração do Webhook e Servidor (FastAPI) ---

# Inicializa o Application do bot
application = Application.builder().token(TOKEN).build()

# Inicializa o servidor web FastAPI
app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request):
    """O endpoint que o Telegram chama."""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Erro no endpoint do webhook: {e}")
        return {"status": "error"}, 500

async def main():
    """Configura e inicia o bot."""
    
    # 1. Registrar os handlers no bot
    # (Handler de Novo Membro)
    application.add_handler(ChatMemberHandler(
        handle_new_member, ChatMemberHandler.CHAT_MEMBER
    ))
    
    # (Handler do Clique no Botão)
    application.add_handler(CallbackQueryHandler(
        handle_button_click, pattern="^verify_profile$"
    ))

    # 2. Definir o webhook no Telegram
    webhook_endpoint = f"{WEBHOOK_URL}/webhook"
    logger.info(f"Definindo webhook para: {webhook_endpoint}")
    await application.bot.set_webhook(url=webhook_endpoint)

    # 3. Iniciar o servidor FastAPI
    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    
    logger.info("--- Servidor do Bot iniciado ---")
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
