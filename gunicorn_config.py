import os
import multiprocessing

# Базовые настройки
bind = f"0.0.0.0:{os.environ.get('PORT', '5050')}"
workers = int(os.environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'uvicorn.workers.UvicornWorker'

# Увеличенные таймауты для предотвращения WORKER TIMEOUT
timeout = 120  # увеличено с 30 секунд
graceful_timeout = 90
keepalive = 5

# Лимиты для предотвращения утечек памяти
max_requests = 1000
max_requests_jitter = 50

# Настройки для логирования
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')

# Preload app для экономии памяти
preload_app = True

def on_starting(server):
    """Called just before the master process is initialized."""
    server.log.info("Starting Gunicorn server...")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    server.log.info(f"Worker spawning (pid: {worker.pid})")

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    # Устанавливаем ID воркера для контроля планировщика
    os.environ['APP_WORKER_ID'] = str(worker.age)
    server.log.info(f"Worker spawned (pid: {worker.pid}, id: {worker.age})")

def worker_int(worker):
    """Called just after a worker exited on SIGINT or SIGQUIT."""
    worker.log.info(f"Worker interrupted (pid: {worker.pid})")

def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forking new master process...")
