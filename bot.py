import os
import telebot
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import sqlite3
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
ATERNOS_USER = os.getenv('ATERNOS_USER')
ATERNOS_PASS = os.getenv('ATERNOS_PASS')
ADMIN_ID = os.getenv('ADMIN_ID', '')  # ID администратора для уведомлений

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)

# База данных для ограничений
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_usage (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            last_used TIMESTAMP,
            usage_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def can_user_start_server(user_id, username):
    """Проверка может ли пользователь запустить сервер"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # Получаем данные пользователя
    cursor.execute(
        'SELECT last_used, usage_count FROM user_usage WHERE user_id = ?',
        (user_id,)
    )
    result = cursor.fetchone()
    
    now = datetime.now()
    
    if result:
        last_used = datetime.fromisoformat(result[0])
        usage_count = result[1]
        
        # Проверяем ограничения
        time_diff = now - last_used
        
        # Не чаще чем раз в 5 минут
        if time_diff < timedelta(minutes=5):
            conn.close()
            return False, f"⏳ Следующий запуск через {5 - time_diff.seconds // 60} минут"
        
        # Не более 10 запусков в день
        if usage_count >= 10 and last_used.date() == now.date():
            conn.close()
            return False, "❌ Достигнут лимит 10 запусков в день"
    
    # Обновляем данные пользователя
    if result:
        cursor.execute('''
            UPDATE user_usage 
            SET last_used = ?, usage_count = usage_count + 1, username = ?
            WHERE user_id = ?
        ''', (now.isoformat(), username, user_id))
    else:
        cursor.execute('''
            INSERT INTO user_usage (user_id, username, last_used, usage_count)
            VALUES (?, ?, ?, 1)
        ''', (user_id, username, now.isoformat()))
    
    conn.commit()
    conn.close()
    return True, "✅ Можете запускать сервер"

def setup_driver():
    """Настройка ChromeDriver"""
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def start_aternos_server():
    """Запуск сервера на Aternos"""
    driver = None
    try:
        logger.info("Запуск ChromeDriver для Aternos...")
        driver = setup_driver()
        wait = WebDriverWait(driver, 20)
        
        driver.get("https://aternos.org/go/")
        
        # Логин
        username_field = wait.until(EC.element_to_be_clickable((By.ID, "user")))
        username_field.send_keys(ATERNOS_USER)
        
        password_field = driver.find_element(By.ID, "password")
        password_field.send_keys(ATERNOS_PASS)
        
        login_button = driver.find_element(By.ID, "login")
        login_button.click()
        
        time.sleep(8)
        
        # Проверка ошибки входа
        try:
            error_msg = driver.find_element(By.CLASS_NAME, "error")
            if error_msg.is_displayed():
                return False, "❌ Ошибка входа в Aternos"
        except NoSuchElementException:
            pass
        
        # Запуск сервера
        start_selector = "button.start"
        start_button = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, start_selector)))
        
        # Проверка статуса
        status_element = driver.find_element(By.CLASS_NAME, "statuslabel")
        status_text = status_element.text.lower()
        
        if 'online' in status_text:
            return True, "🟢 Сервер уже запущен! Можешь заходить!"
        elif 'offline' in status_text and start_button.is_enabled():
            start_button.click()
            time.sleep(3)
            
            # Подтверждение если нужно
            try:
                confirm_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-confirm")
                if confirm_btn.is_displayed():
                    confirm_btn.click()
            except NoSuchElementException:
                pass
            
            return True, "✅ Сервер запускается! Зайдет через 2-5 минут 🚀"
        else:
            return False, "⏳ Сервер в очереди или обновляется"
            
    except TimeoutException:
        return False, "⏰ Aternos не отвечает, попробуй позже"
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        return False, f"❌ Ошибка: {str(e)}"
    finally:
        if driver:
            driver.quit()

def notify_admin(user_info, result):
    """Уведомление администратора о запуске"""
    if ADMIN_ID:
        try:
            admin_text = f"👤 {user_info}\n🔄 {result}"
            bot.send_message(ADMIN_ID, admin_text)
        except Exception as e:
            logger.error(f"Ошибка уведомления админа: {e}")

# Команды бота
@bot.message_handler(commands=['start'])
def start_command(message):
    user = message.from_user
    welcome_text = f"""
🎮 **Добро пожаловать на Vibecraft, {user.first_name}!**

Ты можешь запустить наш Minecraft сервер когда захочешь поиграть!

**Команды:**
/startserver - Запустить сервер
/status - Статус сервера  
/rules - Правила использования
/help - Помощь

⚡ Сервер запускается 2-5 минут
🎯 После запуска заходи по IP из дискорда
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['startserver'])
def start_server_command(message):
    user = message.from_user
    user_id = user.id
    username = f"@{user.username}" if user.username else user.first_name
    
    logger.info(f"Запрос запуска от {username} (ID: {user_id})")
    
    # Проверяем ограничения
    can_start, reason = can_user_start_server(user_id, username)
    
    if not can_start:
        bot.reply_to(message, reason)
        return
    
    # Отправляем сообщение о начале процесса
    processing_msg = bot.reply_to(message, 
        f"🔄 {username}, запускаю сервер...\n"
        "Ожидай 10-20 секунд ⏳"
    )
    
    # Запускаем сервер
    success, result = start_aternos_server()
    
    # Отправляем результат
    result_text = f"{result}\n\n👤 Запросил: {username}"
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=processing_msg.message_id,
        text=result_text
    )
    
    # Уведомляем администратора
    user_info = f"{username} (ID: {user_id})"
    notify_admin(user_info, result)

@bot.message_handler(commands=['status'])
def status_command(message):
    status_text = """
📊 **Статус Vibecraft**

🟢 Бот активен
⚡ Сервер: Aternos
🎮 Версия: 1.20.1
👥 Игроки: 20 онлайн

💡 Используй /startserver чтобы запустить сервер
🔗 IP получи в нашем дискорде
    """
    bot.reply_to(message, status_text, parse_mode='Markdown')

@bot.message_handler(commands=['rules'])
def rules_command(message):
    rules_text = """
📋 **Правила использования:**

✅ Можно запускать сервер когда хочешь играть
⏳ Не чаще 1 раза в 5 минут  
🎯 Не более 10 запусков в день
🚫 Не спамить командами

⚡ Сервер запускается 2-5 минут
🎮 После запуска - заходи играть!
    """
    bot.reply_to(message, rules_text, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
🆘 **Помощь по боту**

/start - Начать работу
/startserver - Запустить сервер
/status - Информация о сервере  
/rules - Правила использования
/help - Эта справка

❓ **Частые вопросы:**
Q: Сколько ждать запуск?
A: 2-5 минут

Q: Где взять IP сервера?
A: В нашем Discord сервере

Q: Сервер не запускается?
A: Напиши админу
    """
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def stats_command(message):
    """Статистика использования (только для админа)"""
    if str(message.from_user.id) != ADMIN_ID:
        bot.reply_to(message, "❌ Эта команда только для администратора")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM user_usage')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(usage_count) FROM user_usage')
    total_starts = cursor.fetchone()[0] or 0
    
    cursor.execute('''
        SELECT username, usage_count, last_used 
        FROM user_usage 
        ORDER BY usage_count DESC 
        LIMIT 10
    ''')
    top_users = cursor.fetchall()
    
    conn.close()
    
    stats_text = f"""
📈 **Статистика бота**

👥 Всего пользователей: {total_users}
🚀 Всего запусков: {total_starts}

🏆 Топ пользователей:
"""
    
    for i, (username, count, last_used) in enumerate(top_users, 1):
        last_used_time = datetime.fromisoformat(last_used).strftime("%d.%m %H:%M")
        stats_text += f"{i}. {username}: {count} запусков ({last_used_time})\n"
    
    bot.reply_to(message, stats_text, parse_mode='Markdown')

# Обработка обычных сообщений
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, 
        "🤔 Используй команды для управления сервером!\n"
        "Напиши /help для списка команд"
    )

# Инициализация и запуск
if __name__ == '__main__':
    logger.info("=== Запуск Vibecraft Bot ===")
    init_db()
    logger.info("База данных инициализирована")
    
    try:
        logger.info("Бот запускается...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
        time.sleep(60)