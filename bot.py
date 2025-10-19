import threading
from flask import Flask

# Certifique-se de que 'os' também está importado
import os
import logging
import sqlite3
import os
from datetime import datetime, time, timedelta
import pytz  # Para lidar com fuso horário

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- Configuração Inicial ---

# Configure o logging para ver erros no terminal
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Token do BotFather (COLOQUE O SEU AQUI)
# (Melhor prática é usar variáveis de ambiente, mas para simplificar, colocamos aqui)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROUP_CHAT_ID_STR = os.environ.get("GROUP_CHAT_ID")
ADMIN_USER_IDS_STR = os.environ.get("ADMIN_USER_IDS")  # Ex: "123,456,789"

# --- Verificações de Segurança ---
if not TELEGRAM_TOKEN:
    logger.critical("Variável de ambiente TELEGRAM_TOKEN não definida! Saindo.")
    exit()

if not GROUP_CHAT_ID_STR:
    logger.critical("Variável de ambiente GROUP_CHAT_ID não definida! Saindo.")
    exit()

if not ADMIN_USER_IDS_STR:
    logger.warning("ADMIN_USER_IDS não definida. Comandos de debug não funcionarão.")
    ADMIN_USER_IDS = []
else:
    # Converte a string "123,456" para uma lista de números [123, 456]
    try:
        ADMIN_USER_IDS = [
            int(admin_id.strip()) for admin_id in ADMIN_USER_IDS_STR.split(",")
        ]
    except ValueError:
        logger.critical(
            "ADMIN_USER_IDS tem um formato inválido. Use números separados por vírgula."
        )
        ADMIN_USER_IDS = []

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
except ValueError:
    logger.critical("GROUP_CHAT_ID não é um número válido.")
    exit()


# Fuso horário de Brasília
TIMEZONE = pytz.timezone("America/Sao_Paulo")

# Estados para a conversa de edição de horário
(STATE_SELECT_SCHEDULE, STATE_GET_DAY, STATE_GET_TIME) = range(3)

# IDs dos usuários iniciais (Opcional, mas facilita o setup)
# Você pode descobrir seu ID falando com o bot @userinfobot
INITIAL_USERS = {
    "João": {
        "id": 0,
        "schedules": [("wednesday", time(21, 0)), ("sunday", time(17, 0))],
    },
    "Victor": {
        "id": 0,
        "schedules": [("sunday", time(17, 0)), ("wednesday", time(20, 0))],
    },
}
# NOTA: O ID 0 é um placeholder. O bot vai pegar o ID real quando o /start for usado no grupo.

# --- Funções do Banco de Dados (SQLite) ---


def init_db():
    """Cria as tabelas do banco de dados se não existirem."""
    conn = sqlite3.connect("data/bot.db")
    cursor = conn.cursor()

    # Tabela de Usuários
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        first_name TEXT
    )
    """
    )

    # Tabela de Agendamentos
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS schedules (
        schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day_of_week TEXT NOT NULL, 
        time_of_day TEXT NOT NULL, 
        job_id_reminder TEXT,     
        job_id_prompt TEXT,       
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """
    )

    # Tabela de Submissões (Comprovantes de Hábito)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS submissions (
        submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        timestamp DATETIME NOT NULL,
        points_awarded INTEGER NOT NULL,
        week_num INTEGER NOT NULL,
        cycle_num INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """
    )

    # Tabela de Dívidas (Aposta Semanal)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS debts (
        debt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        week_num INTEGER NOT NULL,
        amount REAL NOT NULL,
        message_id_to_reply INTEGER,
        paid INTEGER DEFAULT 0,      
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """
    )

    # Tabela do Pote (Contabilidade)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS pote (
        deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        timestamp DATETIME NOT NULL,
        cycle_num INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """
    )

    # Tabela de Ciclos (Sprints de 2 meses)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS cycles (
        cycle_num INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        winner_user_id INTEGER,
        is_active INTEGER DEFAULT 1
    )
    """
    )

    conn.commit()
    conn.close()
    logger.info("Banco de dados inicializado.")


def db_execute(query, params=()):
    """Função helper para executar comandos no DB."""
    try:
        conn = sqlite3.connect("data/bot.db")
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        conn.close()
        return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Erro no DB (write): {e}")
        return None


def db_query_one(query, params=()):
    """Função helper para buscar um resultado no DB."""
    try:
        conn = sqlite3.connect("data/bot.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchone()
        conn.close()
        return result
    except sqlite3.Error as e:
        logger.error(f"Erro no DB (query_one): {e}")
        return None


def db_query_all(query, params=()):
    """Função helper para buscar múltiplos resultados no DB."""
    try:
        conn = sqlite3.connect("data/bot.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchall()
        conn.close()
        return result
    except sqlite3.Error as e:
        logger.error(f"Erro no DB (query_all): {e}")
        return None


# --- Funções Principais do Agendador (APScheduler) ---


def get_current_week():
    """Retorna o número da semana do ano (ISO)."""
    return datetime.now(TIMEZONE).isocalendar()[1]


def get_current_cycle():
    """Retorna o ciclo ativo ou cria um novo."""
    now = datetime.now(TIMEZONE).date()
    cycle = db_query_one(
        "SELECT * FROM cycles WHERE is_active = 1 AND start_date <= ? AND end_date >= ?",
        (now, now),
    )

    if cycle:
        return cycle["cycle_num"]

    # Se não há ciclo ativo, cria um novo
    db_execute("UPDATE cycles SET is_active = 0")  # Desativa antigos

    # Lógica de início: 01/11/25
    # Este é um exemplo, você pode querer uma lógica mais robusta
    start_date = datetime(2025, 10, 1).date()
    # Encontra o ciclo certo baseado na data de hoje
    while True:
        end_date = (start_date + timedelta(days=60)).replace(day=1) - timedelta(
            days=1
        )  # Aproximação de 2 meses
        if start_date <= now <= end_date:
            break
        start_date = end_date + timedelta(days=1)

        # Lógica de parada 31/12/26
        if start_date > datetime(2026, 12, 31).date():
            logger.warning("O período do desafio terminou.")
            return None

    new_cycle_id = db_execute(
        "INSERT INTO cycles (start_date, end_date, is_active) VALUES (?, ?, 1)",
        (start_date, end_date),
    )
    logger.info(f"Novo ciclo {new_cycle_id} criado. De {start_date} até {end_date}")
    return new_cycle_id


async def send_reminder(context: Application, user_id: int, chat_id: int):
    """Envia o lembrete de 15 minutos."""
    user = db_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    if user:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ei <a href='tg://user?id={user_id}'>{user['first_name']}</a>, seu horário de dedicação começa em 15 minutos! 🚀",
            parse_mode=ParseMode.HTML,
        )


async def send_prompt(context: Application, user_id: int, chat_id: int):
    """Envia o pedido de comprovante na hora H."""
    user = db_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    if user:
        # Armazena que este usuário está na "janela de 1 hora"
        # Usamos context.bot_data, que é editável a partir do Application
        # Criamos uma chave única combinando chat_id e user_id
        prompt_key = f"prompt_{chat_id}_{user_id}"

        context.bot_data[prompt_key] = {"time": datetime.now(TIMEZONE)}

        logger.info(f"Janela de prompt ativada para {prompt_key}")

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Olá <a href='tg://user?id={user_id}'>{user['first_name']}</a>! 🌟\n\nÉ hora de começar sua 1h de foco no projeto. Você tem 1 hora a partir de agora para me enviar um print + descrição do que está fazendo para ganhar <b>5 pontos</b>.\n\nBoa sorte!",
            parse_mode=ParseMode.HTML,
        )


async def run_weekly_report(context: Application):
    """Roda no final do Domingo. Calcula pontos, dívidas e envia o leaderboard."""
    chat_id = GROUP_CHAT_ID
    week_num = get_current_week()
    cycle_num = get_current_cycle()
    if not cycle_num:
        return

    logger.info(f"Rodando relatório semanal para a semana {week_num}...")

    users = db_query_all("SELECT * FROM users")
    if not users:
        return

    leaderboard = []
    debts_to_create = []

    for user in users:
        user_id = user["user_id"]
        # Calcula pontos da semana
        points_row = db_query_one(
            "SELECT SUM(points_awarded) as total FROM submissions WHERE user_id = ? AND week_num = ? AND cycle_num = ?",
            (user_id, week_num, cycle_num),
        )
        points_this_week = points_row["total"] if points_row["total"] else 0

        # Calcula aposta (dívida)
        debt_amount = max(0, 50 - (points_this_week * 5))

        leaderboard.append(
            {
                "name": user["first_name"],
                "points": points_this_week,
                "debt": debt_amount,
            }
        )

        if debt_amount > 0:
            debts_to_create.append({"user_id": user_id, "amount": debt_amount})

    # Ordena o leaderboard
    leaderboard.sort(key=lambda x: x["points"], reverse=True)

    # Monta a mensagem do leaderboard
    text = f"🏆 <b>Leaderboard da Semana {week_num}</b> 🏆\n\n"
    for i, entry in enumerate(leaderboard):
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
        text += f"{emoji} {entry['name']}: {entry['points']} pontos\n"

    text += "\n---\n\n💸 <b>Aposta da Semana</b> 💸\n"

    if not debts_to_create:
        text += "Parabéns, ninguém precisa depositar nada esta semana! 🥳"
    else:
        text += "Valores a depositar no pote (caixinha):\n"
        for entry in leaderboard:
            if entry["debt"] > 0:
                text += f"• {entry['name']}: R$ {entry['debt']:.2f}\n"
        text += "\nPor favor, enviem o comprovante do PIX/depósito respondendo à mensagem de cobrança que vou enviar a seguir."

    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
    )

    # Envia as mensagens de cobrança individuais e salva no DB
    for debt in debts_to_create:
        user_id = debt["user_id"]
        amount = debt["amount"]
        user = db_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"<a href='tg://user?id={user_id}'>{user['first_name']}</a>, sua contribuição ... é de <b>R$ {amount:.2f}</b>. \n\nPor favor, responda a esta mensagem com o comprovante.",
            parse_mode=ParseMode.HTML,
        )

        # Salva a dívida no banco com o ID da mensagem para futura verificação
        db_execute(
            "INSERT INTO debts (user_id, week_num, amount, message_id_to_reply, paid) VALUES (?, ?, ?, ?, 0)",
            (user_id, week_num, amount, msg.message_id),
        )


async def run_daily_pote_report(context: Application):
    """Envia a contabilidade do pote no final do dia."""
    chat_id = GROUP_CHAT_ID
    cycle_num = get_current_cycle()
    if not cycle_num:
        return

    total_row = db_query_one(
        "SELECT SUM(amount) as total FROM pote WHERE cycle_num = ?", (cycle_num,)
    )
    total_in_pote = total_row["total"] if total_row["total"] else 0.0

    contributions = db_query_all(
        """
        SELECT u.first_name, SUM(p.amount) as total_contributed
        FROM pote p
        JOIN users u ON p.user_id = u.user_id
        WHERE p.cycle_num = ?
        GROUP BY u.user_id
    """,
        (cycle_num,),
    )

    text = f"💰 <b>Contabilidade do Pote (Ciclo {cycle_num})</b> 💰\n\n"
    text += f"<b>Total Acumulado no Pote: R$ {total_in_pote:.2f}</b>\n\n"
    text += "Contribuições individuais neste ciclo:\n"

    if not contributions:
        text += "Ninguém depositou nada ainda."
    else:
        for c in contributions:
            text += f"• {c['first_name']}: R$ {c['total_contributed']:.2f}\n"

    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
    )


async def run_bi_monthly_cycle_end(context: Application):
    """Roda a cada 2 meses. Encontra o vencedor, anuncia e zera o pote (contabilidade)."""
    chat_id = GROUP_CHAT_ID
    cycle_num = get_current_cycle()
    if not cycle_num:
        return

    logger.info(f"Finalizando ciclo {cycle_num}...")

    # Encontra o vencedor do ciclo
    winner = db_query_one(
        """
        SELECT u.user_id, u.first_name, SUM(s.points_awarded) as total_points
        FROM submissions s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.cycle_num = ?
        GROUP BY u.user_id
        ORDER BY total_points DESC
        LIMIT 1
    """,
        (cycle_num,),
    )

    # Pega o total do pote
    total_row = db_query_one(
        "SELECT SUM(amount) as total FROM pote WHERE cycle_num = ?", (cycle_num,)
    )
    total_in_pote = total_row["total"] if total_row["total"] else 0.0

    text = f"🎉 <b>FIM DO CICLO {cycle_num}</b> 🎉\n\n"

    if winner and total_in_pote > 0:
        winner_id = winner["user_id"]
        winner_name = winner["first_name"]
        winner_points = winner["total_points"]

        text += f"O grande vencedor do ciclo é <b>{winner_name}</b> com <b>{winner_points}</b> pontos!\n\n"
        text += f"Parabéns <a href='tg://user?id={winner_id}'>{winner_name}</a>, você resgata o prêmio total de <b>R$ {total_in_pote:.2f}</b>! 🤑"

        # Atualiza o ciclo como finalizado e com vencedor
        db_execute(
            "UPDATE cycles SET winner_user_id = ?, is_active = 0 WHERE cycle_num = ?",
            (winner_id, cycle_num),
        )

    elif winner:
        text += f"O ciclo terminou, e o vencedor em pontos foi <b>{winner['first_name']}</b> com {winner['total_points']} pontos.\n\n"
        text += "Como o pote está zerado, não há prêmio em dinheiro. Mas parabéns pela disciplina!"
        db_execute(
            "UPDATE cycles SET winner_user_id = ?, is_active = 0 WHERE cycle_num = ?",
            (winner["user_id"], cycle_num),
        )
    else:
        text += "O ciclo terminou sem vencedores ou pontos registrados. O pote de R$ {total_in_pote:.2f} será zerado."
        db_execute("UPDATE cycles SET is_active = 0 WHERE cycle_num = ?", (cycle_num,))

    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
    )

    # Cria o próximo ciclo (a função get_current_cycle() fará isso automaticamente na próxima vez que for chamada)
    get_current_cycle()


def schedule_user_jobs(
    scheduler: AsyncIOScheduler, user_id: int, chat_id: int, application: Application
):
    """Lê os horários do DB e agenda os lembretes para um usuário."""
    schedules = db_query_all("SELECT * FROM schedules WHERE user_id = ?", (user_id,))

    # Mapeia dia da semana (texto) para o formato do cron (inglês)
    day_map = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }

    for s in schedules:
        schedule_id = s["schedule_id"]
        day_str = s["day_of_week"].lower()
        time_str = s["time_of_day"]

        if day_str not in day_map:
            logger.warning(
                f"Dia da semana inválido '{day_str}' para schedule_id {schedule_id}"
            )
            continue

        try:
            time_obj = time.fromisoformat(time_str)
            day_cron = day_map[day_str]

            # --- Agenda o Lembrete (15 min antes) ---
            reminder_time = (
                datetime.combine(datetime.today(), time_obj) - timedelta(minutes=15)
            ).time()
            job_id_reminder = f"reminder_{schedule_id}"

            scheduler.add_job(
                send_reminder,
                trigger=CronTrigger(
                    day_of_week=day_cron,
                    hour=reminder_time.hour,
                    minute=reminder_time.minute,
                    timezone=TIMEZONE,
                ),
                id=job_id_reminder,
                replace_existing=True,
                kwargs={"context": application, "user_id": user_id, "chat_id": chat_id},
            )

            # --- Agenda o Prompt (Hora H) ---
            job_id_prompt = f"prompt_{schedule_id}"
            scheduler.add_job(
                send_prompt,
                trigger=CronTrigger(
                    day_of_week=day_cron,
                    hour=time_obj.hour,
                    minute=time_obj.minute,
                    timezone=TIMEZONE,
                ),
                id=job_id_prompt,
                replace_existing=True,
                kwargs={"context": application, "user_id": user_id, "chat_id": chat_id},
            )

            # Salva os Job IDs no DB para poder editá-los/removê-los
            db_execute(
                "UPDATE schedules SET job_id_reminder = ?, job_id_prompt = ? WHERE schedule_id = ?",
                (job_id_reminder, job_id_prompt, schedule_id),
            )
            logger.info(
                f"Agendado {day_cron} às {time_str} (e lembrete) para user {user_id}"
            )

        except Exception as e:
            logger.error(f"Erro ao agendar job para schedule {schedule_id}: {e}")


def schedule_global_jobs(
    scheduler: AsyncIOScheduler, chat_id: int, application: Application
):
    """Agenda os relatórios semanais, diários e de ciclo."""

    # Relatório Semanal - Domingo 23:00
    scheduler.add_job(
        run_weekly_report,
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=0, timezone=TIMEZONE),
        id=f"weekly_report_{chat_id}",
        replace_existing=True,
        data={"chat_id": chat_id},
        kwargs={"context": application},
    )

    # Contabilidade Diária - Todo dia 22:00
    scheduler.add_job(
        run_daily_pote_report,
        trigger=CronTrigger(hour=22, minute=0, timezone=TIMEZONE),
        id=f"daily_pote_report_{chat_id}",
        replace_existing=True,
        data={"chat_id": chat_id},
        kwargs={"context": application},
    )

    # Fim do Ciclo - A cada 2 meses, no último dia do mês, às 23:30
    scheduler.add_job(
        run_bi_monthly_cycle_end,
        trigger=CronTrigger(
            day="last", month="*/2", hour=23, minute=30, timezone=TIMEZONE
        ),
        id=f"cycle_end_{chat_id}",
        replace_existing=True,
        data={"chat_id": chat_id},
        kwargs={"context": application},
    )
    logger.info(
        f"Agendados jobs globais (semanal, diário, ciclo) para o chat {chat_id}"
    )


# --- Comandos do Bot (Handlers) ---


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /start - Configura usuários iniciais no banco DE FORMA SEGURA.
    Só adiciona horários padrões se o usuário ainda não tiver nenhum.
    """
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("Olá! Por favor, me adicione a um grupo.")
        return

    # Verifica se o chat é o grupo correto
    if chat.id != GROUP_CHAT_ID:
        await update.message.reply_text("Este grupo não está autorizado.")
        logger.warning(f"Comando /start recebido de um chat não autorizado: {chat.id}")
        return

    user = update.effective_user

    await update.message.reply_text(
        f"Olá, {user.first_name}! Verificando configuração inicial...\n"
        "Este comando (/start) agora é seguro e só adicionará horários "
        "padrões para usuários novos."
    )

    # --- Lógica de Cadastro Inicial ---

    # Registra o usuário que deu /start (não faz mal rodar de novo)
    db_execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
        (user.id, user.username, user.first_name),
    )

    # Tenta mapear o ID de quem deu /start para os nomes no dicionário
    if "joão" in user.first_name.lower() or "joao" in user.first_name.lower():
        INITIAL_USERS["João"]["id"] = user.id
    if "victor" in user.first_name.lower() or "vitor" in user.first_name.lower():
        INITIAL_USERS["Victor"]["id"] = user.id

    users_processed_count = 0

    for name, data in INITIAL_USERS.items():
        user_id = data["id"]

        # Só processa se soubermos o ID do usuário
        if user_id != 0:

            # Garante que o usuário está na tabela 'users'
            db_execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, f"user_{user_id}", name),  # Adiciona um username placeholder
            )

            # 1. Verifica se o usuário JÁ TEM horários
            existing_schedules = db_query_one(
                "SELECT COUNT(*) as count FROM schedules WHERE user_id = ?", (user_id,)
            )

            # 2. SÓ ADICIONA SE O COUNT FOR ZERO
            if existing_schedules and existing_schedules["count"] == 0:
                logger.info(
                    f"Usuário {name} (ID: {user_id}) não tem horários. Adicionando defaults..."
                )
                users_processed_count += 1

                for day_name, time_obj in data["schedules"]:
                    db_execute(
                        "INSERT INTO schedules (user_id, day_of_week, time_of_day) VALUES (?, ?, ?)",
                        (user_id, day_name, time_obj.strftime("%H:%M")),
                    )
            else:
                # Se o usuário já tem horários (count > 0), não fazemos NADA.
                logger.info(
                    f"Usuário {name} (ID: {user_id}) já possui horários. Nenhum default foi adicionado."
                )

    if users_processed_count > 0:
        await update.message.reply_text(
            f"{users_processed_count} usuário(s) tiveram seus horários padrões definidos no banco.\n"
            "Reinicie o bot para carregar os novos agendamentos."
        )
    else:
        await update.message.reply_text(
            "Tudo certo. Os usuários já existentes não foram modificados."
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa envios de fotos (Comprovantes de Hábito ou PIX)."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    # Registra o usuário se for a primeira vez que ele interage
    db_execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
        (user.id, user.username, user.first_name),
    )

    # --- Lógica 1: É um Comprovante de PIX? ---
    if message.reply_to_message:
        reply_msg_id = message.reply_to_message.message_id

        # Verifica se é resposta a uma cobrança de dívida
        debt = db_query_one(
            "SELECT * FROM debts WHERE message_id_to_reply = ? AND user_id = ? AND paid = 0",
            (reply_msg_id, user.id),
        )

        if debt:
            amount = debt["amount"]
            week_num = debt["week_num"]
            cycle_num = get_current_cycle()

            # Marca como pago
            db_execute(
                "UPDATE debts SET paid = 1 WHERE debt_id = ?", (debt["debt_id"],)
            )

            # Adiciona ao pote
            db_execute(
                "INSERT INTO pote (user_id, amount, timestamp, cycle_num) VALUES (?, ?, ?, ?)",
                (user.id, amount, datetime.now(TIMEZONE), cycle_num),
            )

            total_row = db_query_one(
                "SELECT SUM(amount) as total FROM pote WHERE cycle_num = ?",
                (cycle_num,),
            )
            total_in_pote = total_row["total"] if total_row["total"] else 0.0

            await message.reply_text(
                f"✅ Pagamento ... (Ciclo {cycle_num}): <b>R$ {total_in_pote:.2f}</b>",
                f"Total no pote (Ciclo {cycle_num}): <b>R$ {total_in_pote:.2f}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

    # --- Lógica 2: É um Comprovante de Hábito? ---

    # Verifica se o usuário está na janela de 1h (5 pontos)
    # A flag agora está em bot_data, com a chave (chat_id)_(user_id)
    prompt_key = f"prompt_{chat.id}_{user.id}"

    if prompt_key in context.bot_data:
        prompt_time = context.bot_data[prompt_key]["time"]
        time_diff = datetime.now(TIMEZONE) - prompt_time

        if time_diff.total_seconds() < 3600:  # Dentro de 1 hora
            points_to_award = 5
            del context.bot_data[prompt_key]  # Remove a flag para não pontuar duplo
        else:
            # Já passou de 1h, mas ainda estava na "janela"
            points_to_award = 3  # Fora do horário
            del context.bot_data[prompt_key]
    else:
        # Envio fora da janela (3 pontos)
        points_to_award = 3

    # Verifica limite de 2 por semana
    week_num = get_current_week()
    cycle_num = get_current_cycle()

    if not cycle_num:
        await message.reply_text(
            "Erro: Não há um ciclo de desafio ativo no momento. O desafio ainda não começou ou já terminou."
        )
        return

    submissions_row = db_query_one(
        "SELECT COUNT(*) as count FROM submissions WHERE user_id = ? AND week_num = ? AND cycle_num = ?",
        (user.id, week_num, cycle_num),
    )
    submissions_this_week = submissions_row["count"] if submissions_row else 0

    if submissions_this_week >= 2:
        await message.reply_text(
            f"Limite atingido! {user.first_name}, você já enviou seus 2 comprovantes desta semana."
        )
        return

    # Registra a submissão
    db_execute(
        "INSERT INTO submissions (user_id, timestamp, points_awarded, week_num, cycle_num) VALUES (?, ?, ?, ?, ?)",
        (user.id, datetime.now(TIMEZONE), points_to_award, week_num, cycle_num),
    )

    await message.reply_text(
        f"Comprovante recebido, {user.first_name}! 🥳\n\n"
        f"<b>+{points_to_award} pontos</b> para você!\n"
        f"({submissions_this_week + 1} de 2 esta semana)",
        parse_mode=ParseMode.HTML,
    )


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /leaderboard - Mostra o placar do ciclo atual."""
    cycle_num = get_current_cycle()
    if not cycle_num:
        await update.message.reply_text("Nenhum ciclo de desafio ativo no momento.")
        return

    # ---- ADICIONE ESTA PARTE ----
    # Busca os detalhes do ciclo (start_date, end_date)
    cycle = db_query_one("SELECT * FROM cycles WHERE cycle_num = ?", (cycle_num,))
    if not cycle:
        await update.message.reply_text(
            "Erro: Não consegui encontrar os detalhes do ciclo atual."
        )
        return
    # ---- FIM DA ADIÇÃO ----

    scores = db_query_all(
        """
        SELECT u.first_name, SUM(s.points_awarded) as total_points
        FROM submissions s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.cycle_num = ?
        GROUP BY u.user_id
        ORDER BY total_points DESC
    """,
        (cycle_num,),
    )

    # Agora esta linha (que era a 793) vai funcionar, pois 'cycle' existe
    text = f"🏆 <b>Leaderboard do Ciclo {cycle_num}</b> 🏆\n(de {cycle['start_date']} até {cycle['end_date']})\n\n"
    # ... (resto da função)

    if not scores:
        text += "Ninguém pontuou ainda neste ciclo."
    else:
        for i, score in enumerate(scores):
            emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
            text += f"{emoji} {score['first_name']}: {score['total_points']} pontos\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def pote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /pote - Mostra o status do pote (chama a função de relatório diário)."""
    # Passa o 'application' para a função, que agora espera por ele
    await run_daily_pote_report(context.application)


async def meus_horarios_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /meus_horarios - Mostra os horários agendados do usuário."""
    user_id = update.effective_user.id
    schedules = db_query_all(
        "SELECT * FROM schedules WHERE user_id = ? ORDER BY day_of_week, time_of_day",
        (user_id,),
    )

    if not schedules:
        await update.message.reply_text(
            "Você não tem nenhum horário cadastrado. Use /editar_horario para adicionar."
        )
        return

    # Mapeamento para português
    day_map_pt = {
        "monday": "Segunda",
        "tuesday": "Terça",
        "wednesday": "Quarta",
        "thursday": "Quinta",
        "friday": "Sexta",
        "saturday": "Sábado",
        "sunday": "Domingo",
    }

    text = "Seus horários agendados:\n\n"
    for s in schedules:
        text += f"• <b>{day_map_pt.get(s['day_of_week'], s['day_of_week'].capitalize())}</b> às <b>{s['time_of_day']}</b>\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# --- Comandos Adicionais ---


async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /usuarios - Lista todos os usuários cadastrados."""
    users = db_query_all(
        "SELECT user_id, first_name, username FROM users ORDER BY first_name"
    )

    if not users:
        await update.message.reply_text("Nenhum usuário cadastrado no bot ainda.")
        return

    text = "👥 <b>Usuários no Desafio</b> 👥\n\n"
    for user in users:
        text += f"• {user['first_name']} (@{user['username']})\n    (ID: `{user['user_id']}`)\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


def build_submissions_keyboard(cycle_num: int, page: int = 0):
    """Função helper para criar o teclado paginado de submissões."""
    PAGE_SIZE = 8  # Quantidade de submissões por página
    offset = page * PAGE_SIZE

    # Busca as submissões da página
    submissions = db_query_all(
        """
        SELECT s.submission_id, s.timestamp, s.points_awarded, u.first_name 
        FROM submissions s 
        JOIN users u ON s.user_id = u.user_id 
        WHERE s.cycle_num = ? 
        ORDER BY s.timestamp DESC
        LIMIT ? OFFSET ?
    """,
        (cycle_num, PAGE_SIZE, offset),
    )

    # Conta o total
    total_row = db_query_one(
        "SELECT COUNT(*) as count FROM submissions WHERE cycle_num = ?", (cycle_num,)
    )
    total_subs = total_row["count"] if total_row else 0
    total_pages = max(1, (total_subs + PAGE_SIZE - 1) // PAGE_SIZE)  # Cálculo de teto

    text = f"📋 <b>Submissões do Ciclo {cycle_num}</b> (Pág {page + 1} de {total_pages})\n\n"
    buttons = []

    if not submissions:
        text += "Nenhuma submissão encontrada para este ciclo."

    for sub in submissions:
        # Formata o timestamp (que vem do DB como string ISO)
        try:
            timestamp_dt = datetime.fromisoformat(sub["timestamp"])
            ts_str = timestamp_dt.strftime("%d/%m %H:%M")
        except ValueError:
            ts_str = sub["timestamp"]  # Fallback

        # Adiciona o texto da submissão
        text += f"• `{ts_str}` - {sub['first_name']} (+{sub['points_awarded']}pts)\n"
        # Adiciona o botão de deletar para esta submissão
        buttons.append(
            [
                InlineKeyboardButton(
                    f"❌ Deletar ({ts_str} - {sub['first_name']})",
                    callback_data=f"del_sub_{sub['submission_id']}_{page}",
                )
            ]
        )

    # Adiciona botões de navegação
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ Anterior", callback_data=f"list_subs_page_{page - 1}"
            )
        )
    if (page + 1) < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                "Próxima ➡️", callback_data=f"list_subs_page_{page + 1}"
            )
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    return InlineKeyboardMarkup(buttons), text


async def list_submissions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /submissoes - Lista submissões do ciclo com botões para deletar."""
    cycle_num = get_current_cycle()
    if not cycle_num:
        await update.message.reply_text("Nenhum ciclo de desafio ativo no momento.")
        return

    keyboard, text = build_submissions_keyboard(cycle_num, page=0)
    await update.message.reply_text(
        text, reply_markup=keyboard, parse_mode=ParseMode.HTML
    )


async def submission_button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Processa cliques nos botões de paginação ou deleção de submissões."""
    query = update.callback_query
    await query.answer()  # Responde ao clique

    data = query.data
    cycle_num = get_current_cycle()

    if not cycle_num:
        await query.edit_message_text("O ciclo já foi encerrado.")
        return

    # --- Lógica de Paginação ---
    if data.startswith("list_subs_page_"):
        page = int(data.split("_")[3])
        keyboard, text = build_submissions_keyboard(cycle_num, page)
        try:
            await query.edit_message_text(
                text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.info(
                f"Erro ao editar mensagem de paginação (provavelmente sem alteração): {e}"
            )

    # --- Lógica de Confirmação de Deleção ---
    elif data.startswith("del_sub_confirm_"):
        parts = data.split("_")
        submission_id = int(parts[3])
        page_to_return = int(parts[4])

        # Deleta do DB
        db_execute("DELETE FROM submissions WHERE submission_id = ?", (submission_id,))

        await query.edit_message_text("✅ Submissão deletada com sucesso.")

        # Envia a lista atualizada
        keyboard, text = build_submissions_keyboard(cycle_num, page_to_return)
        await query.message.reply_text(
            f"Lista de submissões atualizada:\n\n{text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

    # --- Lógica de Deleção (1º clique, pede confirmação) ---
    elif data.startswith("del_sub_"):
        parts = data.split("_")
        submission_id = int(parts[2])
        page = int(parts[3])

        # Pede confirmação
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ SIM, DELETAR AGORA",
                        callback_data=f"del_sub_confirm_{submission_id}_{page}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ NÃO, VOLTAR", callback_data=f"list_subs_page_{page}"
                    )
                ],
            ]
        )
        await query.edit_message_text(
            "⚠️ <b>Tem certeza?</b>\n\nEsta ação não pode ser desfeita e irá recalcular os pontos e dívidas na próxima vez que o relatório semanal rodar.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )


# --- Comandos de Debug (Somente Admins) ---


async def debug_check_admin(update: Update) -> bool:
    """Função helper para checar se o usuário é admin."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text(
            "⛔ Você não tem permissão para usar este comando."
        )
        logger.warning(
            f"Tentativa de uso de comando admin negada para o user_id: {user_id}"
        )
        return False
    return True


async def debug_weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /debug_weekly - Roda o relatório semanal manualmente."""
    if not await debug_check_admin(update):
        return

    await update.message.reply_text("Executando relatório semanal manualmente... ⏳")
    try:
        await run_weekly_report(context.application)
        await update.message.reply_text("✅ Relatório semanal manual concluído.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao rodar relatório semanal: {e}")
        logger.error("Erro no /debug_weekly", exc_info=True)


async def debug_cycle_end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /debug_cycle_end - Roda o fim de ciclo manualmente."""
    if not await debug_check_admin(update):
        return

    await update.message.reply_text("Executando fim de ciclo manualmente... ⏳")
    try:
        await run_bi_monthly_cycle_end(context.application)
        await update.message.reply_text("✅ Fim de ciclo manual concluído.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao rodar fim de ciclo: {e}")
        logger.error("Erro no /debug_cycle_end", exc_info=True)


async def debug_list_jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /debug_jobs - Lista todos os jobs no agendador."""
    if not await debug_check_admin(update):
        return

    scheduler = context.application.bot_data.get("scheduler")
    if not scheduler or not scheduler.running:
        await update.message.reply_text(
            "Agendador não está rodando ou não foi encontrado."
        )
        return

    jobs = scheduler.get_jobs()
    if not jobs:
        await update.message.reply_text("Nenhum job agendado no momento.")
        return

    text = f"Jobs Agendados (Total: {len(jobs)}):\n\n"
    for job in jobs:
        text += (
            f"• <b>ID:</b> `{job.id}`\n"
            f"  <b>Próxima Execução:</b> `{job.next_run_time}`\n"
            f"  <b>Função:</b> `{job.func.__name__}`\n\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def debug_cycle_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /debug_cycle - Mostra infos do ciclo atual."""
    if not await debug_check_admin(update):
        return

    cycle_num = get_current_cycle()
    if not cycle_num:
        await update.message.reply_text(
            "get_current_cycle() retornou None. Nenhum ciclo ativo."
        )
        return

    cycle = db_query_one("SELECT * FROM cycles WHERE cycle_num = ?", (cycle_num,))
    if not cycle:
        await update.message.reply_text(
            f"Ciclo {cycle_num} não encontrado no banco de dados."
        )
        return

    text = (
        f"<b>Informações do Ciclo Ativo</b>\n"
        f"<b>Número:</b> {cycle['cycle_num']}\n"
        f"<b>Início:</b> {cycle['start_date']}\n"
        f"<b>Fim:</b> {cycle['end_date']}\n"
        f"<b>Ativo:</b> {cycle['is_active']}\n"
        f"<b>Vencedor:</b> {cycle['winner_user_id'] or 'N/A'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# --- Lógica da Conversa de Edição de Horário ---


async def edit_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia a conversa para editar/adicionar um horário."""
    user_id = update.effective_user.id
    schedules = db_query_all("SELECT * FROM schedules WHERE user_id = ?", (user_id,))

    # Mapeamento para português
    day_map_pt = {
        "monday": "Segunda",
        "tuesday": "Terça",
        "wednesday": "Quarta",
        "thursday": "Quinta",
        "friday": "Sexta",
        "saturday": "Sábado",
        "sunday": "Domingo",
    }

    buttons = []
    for s in schedules:
        day_pt = day_map_pt.get(s["day_of_week"], s["day_of_week"])
        text = f"{day_pt} - {s['time_of_day']}"
        # O callback_data será o ID do agendamento no DB
        buttons.append(
            [InlineKeyboardButton(text, callback_data=f"edit_{s['schedule_id']}")]
        )

    buttons.append(
        [InlineKeyboardButton("➕ Adicionar Novo Horário", callback_data="add_new")]
    )
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        "Qual horário você gostaria de editar ou adicionar?", reply_markup=reply_markup
    )

    return STATE_SELECT_SCHEDULE


async def select_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usuário selecionou um horário para editar, ou 'adicionar'."""
    query = update.callback_query
    await query.answer()

    user_data = context.user_data  # Armazena dados temporários da conversa

    if query.data == "add_new":
        user_data["action"] = "add"
        user_data["schedule_id"] = None
        await query.edit_message_text("Ok, vamos adicionar um novo horário.")
    elif query.data == "cancel":
        await query.edit_message_text("Edição cancelada.")
        return ConversationHandler.END
    elif query.data.startswith("edit_"):
        schedule_id = int(query.data.split("_")[1])
        user_data["action"] = "edit"
        user_data["schedule_id"] = schedule_id
        await query.edit_message_text("Ok, vamos editar este horário.")

    # Pergunta o dia da semana
    buttons = [
        ["Segunda", "Terça", "Quarta"],
        ["Quinta", "Sexta", "Sábado"],
        ["Domingo"],
        ["❌ Cancelar"],
    ]
    await query.message.reply_text(
        "Qual o novo dia da semana?",
        reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True),
    )

    return STATE_GET_DAY


async def get_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usuário enviou o dia da semana."""
    day_pt = update.message.text
    user_data = context.user_data

    if day_pt == "❌ Cancelar":
        await update.message.reply_text(
            "Edição cancelada.", reply_markup=ReplyKeyboardRemove()
        )
        user_data.clear()
        return ConversationHandler.END

    day_map_en = {
        "Segunda": "monday",
        "Terça": "tuesday",
        "Quarta": "wednesday",
        "Quinta": "thursday",
        "Sexta": "friday",
        "Sábado": "saturday",
        "Domingo": "sunday",
    }

    day_en = day_map_en.get(day_pt)

    if not day_en:
        await update.message.reply_text("Dia inválido. Por favor, use os botões.")
        return STATE_GET_DAY  # Permanece no mesmo estado

    user_data["new_day"] = day_en

    await update.message.reply_text(
        "Entendido. Agora, por favor, me envie a nova hora no formato <b>HH:MM</b> (ex: 21:00 ou 09:30).",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML,
    )

    return STATE_GET_TIME


async def get_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usuário enviou a hora (HH:MM). Finaliza a edição."""
    time_str = update.message.text
    user_data = context.user_data
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    scheduler = context.bot_data["scheduler"]

    try:
        # Valida o formato da hora
        new_time_obj = time.fromisoformat(time_str)
        new_time_str = new_time_obj.strftime("%H:%M")
        new_day = user_data["new_day"]
        action = user_data["action"]
        schedule_id = user_data.get("schedule_id")

        if action == "add":
            # Adiciona novo horário no DB
            db_execute(
                "INSERT INTO schedules (user_id, day_of_week, time_of_day) VALUES (?, ?, ?)",
                (user_id, new_day, new_time_str),
            )
            await update.message.reply_text(
                f"✅ Horário adicionado: {new_day.capitalize()} às {new_time_str}."
            )

        elif action == "edit":
            # Remove jobs antigos
            old_schedule = db_query_one(
                "SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,)
            )
            if old_schedule["job_id_reminder"]:
                scheduler.remove_job(old_schedule["job_id_reminder"])
            if old_schedule["job_id_prompt"]:
                scheduler.remove_job(old_schedule["job_id_prompt"])

            # Atualiza no DB
            db_execute(
                "UPDATE schedules SET day_of_week = ?, time_of_day = ? WHERE schedule_id = ?",
                (new_day, new_time_str, schedule_id),
            )
            await update.message.reply_text(
                f"✅ Horário atualizado para: {new_day.capitalize()} às {new_time_str}."
            )

        # Reagenda os jobs para este usuário
        schedule_user_jobs(scheduler, user_id, chat_id, context.application)

        user_data.clear()  # Limpa os dados temporários
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "Formato de hora inválido. Por favor, envie no formato <b>HH:MM</b> (ex: 21:00).",
            parse_mode=ParseMode.HTML,
        )
        return STATE_GET_TIME  # Permanece no estado de pegar a hora


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela a conversa de edição."""
    context.user_data.clear()
    await update.message.reply_text(
        "Edição cancelada.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def post_init(application: Application):
    """
    Função de hook para rodar na inicialização do bot.
    1. Inicia o scheduler.
    2. Carrega TODOS os agendamentos do banco de dados.
    """

    # 1. Inicia o Scheduler
    scheduler = application.bot_data.get("scheduler")
    if not scheduler:
        logger.error("Scheduler não encontrado no bot_data durante o post_init!")
        return

    try:
        scheduler.start()
        logger.info("APScheduler iniciado com sucesso via hook post_init.")
    except Exception as e:
        logger.warning(f"APScheduler já estava rodando? Erro: {e}")

    # 2. Verifica se o CHAT_ID está configurado
    if "GROUP_CHAT_ID" not in globals() or GROUP_CHAT_ID == 0:
        logger.critical(
            "GROUP_CHAT_ID não está configurado! Os agendamentos não serão carregados."
        )
        return

    chat_id = GROUP_CHAT_ID
    logger.info(f"Carregando agendamentos para o chat ID: {chat_id}...")

    try:
        # 3. Agenda os Jobs Globais (Relatórios)
        schedule_global_jobs(scheduler, chat_id, application)
        logger.info("Agendamentos Globais (semanal, diário, ciclo) carregados.")

        # 4. Agenda os Jobs Individuais (Lembretes)
        users = db_query_all("SELECT user_id FROM users")
        if not users:
            logger.warning(
                "Nenhum usuário no banco de dados. Agendamentos de usuários pulados."
            )
        else:
            logger.info(f"Carregando agendamentos para {len(users)} usuário(s)...")
            for user in users:
                user_id = user["user_id"]
                schedule_user_jobs(scheduler, user_id, chat_id, application)
            logger.info("Agendamentos de usuários carregados com sucesso.")

        # 5. Garante que o ciclo atual existe
        get_current_cycle()

        logger.info("Bot Coach está pronto e totalmente sincronizado.")

    except Exception as e:
        logger.critical(
            f"Falha crítica durante a rotina de post_init: {e}", exc_info=True
        )


# --- Função Principal (Main) ---


def main() -> None:
    """Função principal que inicia o bot."""

    # 1. Inicializa o banco de dados
    init_db()

    # 2. Cria o Application (o "cérebro" do bot)
    application = (
        Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    )

    # 3. Inicia o Agendador (Scheduler)
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # Armazena o scheduler no contexto do bot para ser acessível em qualquer handler
    application.bot_data["scheduler"] = scheduler

    # 4. Define a Conversa de Edição de Horário
    edit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("editar_horario", edit_schedule_start)],
        states={
            STATE_SELECT_SCHEDULE: [CallbackQueryHandler(select_schedule_callback)],
            STATE_GET_DAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_day_callback)
            ],
            STATE_GET_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_time_callback)
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancel_callback),
            CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
        ],
    )

    # 5. Registra todos os Handlers (Comandos)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("pote", pote_command))
    application.add_handler(CommandHandler("meus_horarios", meus_horarios_command))
    application.add_handler(CommandHandler("usuarios", list_users_command))
    application.add_handler(CommandHandler("submissoes", list_submissions_command))
    application.add_handler(
        CallbackQueryHandler(
            submission_button_callback, pattern="^del_sub_|^list_subs_page_"
        )
    )

    application.add_handler(CommandHandler("debug_weekly", debug_weekly_command))
    application.add_handler(CommandHandler("debug_cycle_end", debug_cycle_end_command))
    application.add_handler(CommandHandler("debug_jobs", debug_list_jobs_command))
    application.add_handler(CommandHandler("debug_cycle", debug_cycle_info_command))

    application.add_handler(edit_conv_handler)  # Adiciona a conversa

    # Handler de Fotos (para comprovantes)
    # IMPORTANTE: Precisa vir depois dos comandos
    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, handle_photo)
    )

    # 6. Inicia o Bot
    logger.info("Iniciando o bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
