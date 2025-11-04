import os
import logging
import time
import regex  # Usamos 'regex' em vez de 're' para suportar \p{L}
import uvicorn
import asyncio
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager # Necessário para o novo gerenciamento

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
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "impedirei")

# Configura o logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- Função de Verificação (Sua lógica personalizada) ---

async def check_user_profile(user_id: int, chat_id: int, bot: Bot) -> (bool, str):
    """
    Verifica o perfil do usuário (foto, nome) e retorna o status.
    """
    reasons = []
    try:
        # 1. Obter os dados mais recentes do usuário no chat
        chat_member = await bot.get_chat_member(chat_id, user_id)
        user = chat_member.user

        # 2. Verificar Foto de Perfil (MODIFICADO POR VOCÊ)
        profile_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if profile_photos.total_count == 0:
            reasons.append("sem foto de perfil (ou com ela privada)") # SUA MODIFICAÇÃO

        # 3. Verificar @Username (REMOVIDO POR VOCÊ)
        # if not user.username:
        #     reasons.append("sem nome de usuário")

        # 4. Verificar Nome (o first_name)
        if not regex.search(r'\p{L}', user.first_name):
            reasons.append("nome inválido (apenas emojis ou símbolos)")
        
        # 5. Formatar o motivo
        if not reasons:
            return True, ""
        else:
            return False, " e ".join(reasons)

    except Exception as e:
        logger.error(f"Erro ao verificar perfil {user_id}: {e}")
        return True, "" # Aprova por segurança em caso de erro

# --- Handler 1: Novo Membro no Grupo ---

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Disparado quando um novo membro entra no chat.
    """
    if not (update.chat_member and 
            update.chat_member.new_chat_member.status == ChatMember.MEMBER):
        return

    user = update.chat_member.new_chat_member.user
    chat = update.chat
    bot = context.bot
    logger.info(f"Novo membro {user.first_name} ({user.id}) no chat {chat.id}")

    is_valid, reason_text = await check_user_profile(user.id, chat.id, bot)

    if is_valid:
        logger.info(f"Usuário {user.id} aprovado na verificação.")
        return

    # --- Se o perfil for INVÁLIDO ---
    logger.info(f"Usuário {user.id} REPROVADO: {reason_text}. Mutando.")
    
    # 1. Calcular o tempo de mute (MODIFICADO POR VOCÊ)
    until_date = int(time.time()) + 9999999 # SUA MODIFICAÇÃO (Mute longo)
    
    # 2. Definir as permissões de MUTE
    permissions = ChatPermissions(
        can_send_messages=False, can_send_media_messages=False, can_send_polls=False,
        can_send_other_messages=False, can_add_web_page_previews=False,
        can_change_info=False, can_invite_users=False, can_pin_messages=False
    )
    
    # 3. Executar o Mute
    try:
        await bot.restrict_chat_member(
            chat_id=chat.id, user_id=user.id,
            permissions=permissions, until_date=until_date
        )
    except Exception as e:
        logger.error(f"Falha ao mutar {user.id}: {e}")
        return

    # 4. Construir os botões
    keyboard = [
        [InlineKeyboardButton("✅ Já atualizei meu perfil", callback_data="verify_profile")],
        [InlineKeyboardButton("Suporte", url=f"https://t.me/{ADMIN_USERNAME}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # 5. Montar e enviar a mensagem de aviso (MODIFICADO POR VOCÊ)
    text = (
        f"Olá, <a href='tg://user?id={user.id}'>{user.first_name}</a>! Seja bem-vindo(a).\n\n"
        f"Detectamos que seu perfil está incompleto ({reason_text}).\n\n"
        "Por favor, atualize seu perfil e clique no botão abaixo para liberar seu acesso."
    )
    
    await bot.send_message(
        chat_id=chat.id, text=text,
        parse_mode=ParseMode.HTML, reply_markup=reply_markup
    )

# --- Handler 2: Clique no Botão de Verificação ---

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Disparado quando o usuário clica em "verify_profile".
    """
    query = update.callback_query
    user = query.from_user 
    chat_id = query.message.chat.id
    bot = context.bot
    logger.info(f"Usuário {user.id} clicou no botão de verificação.")

    is_valid, reason = await check_user_profile(user.id, chat_id, bot)

    if is_valid:
        # --- APROVADO ---
        logger.info(f"Usuário {user.id} aprovado na RE-verificação. Desmutando.")
        permissions = ChatPermissions(
            can_send_messages=True, can_send_media_messages=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True,
            can_invite_users=True,
        )
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=user.id, permissions=permissions
        )
        await query.answer(
            "✅ Perfil verificado! Seu acesso foi liberado.", 
            show_alert=True
        )
        try:
            await query.message.delete()
        except Exception as e:
            logger.warn(f"Falha ao apagar mensagem {query.message.id}: {e}")
    else:
        # --- REPROVADO ---
        logger.info(f"Usuário {user.id} falhou na RE-verificação: {reason}")
        # 2. Enviar o Pop-up de Erro
        await query.answer(
            f"Ops! Seu perfil ainda está incompleto. "
            f"Verifique se você adicionou uma foto PÚBLICA e tente novamente.\n\n"
            f"Em caso de engano, contate @{ADMIN_USERNAME}",
            show_alert=True
        )


# --- ESTRUTURA DE INICIALIZAÇÃO CORRIGIDA ---

# 1. Construir o Application (mas não inicializar ainda)
application = (
    Application.builder()
    .token(TOKEN)
    .build()
)

# 2. Definir o "context manager" do FastAPI para lidar com o startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- O QUE FAZER ANTES DO SERVIDOR LIGAR ---
    logger.info("Iniciando o bot...")
    # Registrar os handlers
    application.add_handler(ChatMemberHandler(
        handle_new_member, ChatMemberHandler.CHAT_MEMBER
    ))
    application.add_handler(CallbackQueryHandler(
        handle_button_click, pattern="^verify_profile$"
    ))
    
    # Chamar .initialize() - A CORREÇÃO DO ERRO
    await application.initialize()
    
    # Definir o webhook
    webhook_endpoint = f"{WEBHOOK_URL}/webhook"
    logger.info(f"Definindo webhook para: {webhook_endpoint}")
    await application.bot.set_webhook(url=webhook_endpoint)
    
    logger.info("--- Servidor do Bot iniciado e pronto ---")
    yield # Isso é o ponto em que o servidor fica rodando
    
    # --- O QUE FAZER QUANDO O SERVIDOR DESLIGAR ---
    logger.info("Desligando o bot...")
    await application.bot.delete_webhook() # Limpa o webhook
    await application.shutdown() # Desliga o bot corretamente

# 3. Inicializar o FastAPI com o novo lifespan
app = FastAPI(lifespan=lifespan)

# 4. Definir o endpoint do webhook
@app.post("/webhook")
async def webhook(request: Request):
    """O endpoint que o Telegram chama."""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        
        # Agora esta chamada vai funcionar
        await application.process_update(update)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Erro no endpoint do webhook: {e}")
        # Retorna 200 OK para o Telegram não ficar reenviando
        return {"status": "error_logged"}, 200

# 5. O Render vai usar o Start Command para iniciar o Uvicorn
#    Portanto, o 'if __name__ == "__main__":' não é mais necessário aqui
