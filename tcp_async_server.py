#!/usr/bin/env python3
#-*-coding:utf-8-*-
import sys
import traceback
import logging

import datetime
import socket
import select
import random
import pickle
import redis

from envparse import env

logger = logging.getLogger(__name__)

# Читаем переменные
env.read_envfile()
LOG_FILE = env('LOG_FILE', default='tcp_async_server.log')
LOG_LEVEL = env('LOG_LEVEL', default='INFO')
SERVER_HOST = env('SERVER_HOST', default='localhost')
SERVER_PORT = env('SERVER_PORT', default=5000, cast=int)
MAX_CONNECTIONS = env('MAX_CONNECTIONS', default=10, cast=int)
REDIS_HOST = env('REDIS_HOST', default='localhost')
REDIS_PORT = env('REDIS_PORT', default=6379, cast=int)
REDIS_DB = env('REDIS_DB', default=0, cast=int)

# Добавляем логгер
log_level_names = [
    logging.getLevelName(level)
    for level in (logging.CRITICAL,
                  logging.ERROR,
                  logging.WARNING,
                  logging.INFO,
                  logging.DEBUG,
                  logging.NOTSET)
]
if not LOG_LEVEL in log_level_names:
    print('LOG_LEVEL incorrect, set it to DEBUG')
    LOG_LEVEL = logging.DEBUG

logger.setLevel(LOG_LEVEL)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(formatter)
logger.addHandler(fh)

sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(sh)

SERVER_ADDRESS = (SERVER_HOST, SERVER_PORT)

# Переопределяем параметры переданными аргументами
logger.debug("Argument List: {}".format(sys.argv))
if len(sys.argv) > 1:
  SERVER_ADDRESS = (sys.argv[1], SERVER_PORT)
if len(sys.argv) > 2:
  SERVER_ADDRESS = (SERVER_ADDRESS[0], int(sys.argv[2]))

INPUTS = list()
OUTPUTS = list()

# ----------------
# redis соединение
# ----------------
redis_db = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

def b2str(b):
    """bytes => string"""
    result = ""
    try:
        result = b.decode("utf-8").strip()
        logger.info("{}=>{}\n".format(b, result))
    except:
        pass
    return result

def get_non_blocking_server_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # OSError: [Errno 48] Address already in use
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Без блокировки основного потока
    server.setblocking(0)
    server.bind(SERVER_ADDRESS)
    server.listen(MAX_CONNECTIONS)
    return server

# ----------------------------
# Специальная пакетная команда
# ----------------------------
def special_command(cmd):
    result = {}
    cmd = str(cmd)

    if "ACon" in cmd and "CLOSE" in cmd:
        terminator = "\n"
        if "\r\n" in cmd:
            terminator = "\r\n"

        cmd_arr = cmd.split(terminator)
        for item in (cmd_arr):
            # ---------
            # Заголовок
            # ---------
            if "ACon" in item:
                result['title'] = item

            if "=" in item:
                key, value = item.split("=", 1)
                # -------------------------------
                # номера ячеек, для которых
                # есть пароль и не истекло время,
                # 0 - таких нет
                # -------------------------------
                if key == "ACT_CON":
                    result['numbers'] = []
                    box_numbers = value.split(",")
                    for box in box_numbers:
                        for box_item in box:
                            result['numbers'].append(int(box_item))
                elif key == "ID":
                    result['ID'] = value # ид платы
                elif key == "SEND":
                    result['SEND'] = value # номер сообщения (такой же, как в запросе)
                elif key in (
                    "BOX_ENG", # номера ячеек, для которых есть пароль и не истекло время
                    "BOX_TO", # номера ячеек, для которых истекло время
                    "BOX_ACC", # номера ячеек, к которым был осуществлён доступ
                  ):
                  result[key] = []
                  for digit in value.split(","):
                      if digit.isdigit():
                          result[key].append(int(digit))

    if result:
        result['RESET'] = 0 # 0 - ничего не делать, 1 - перезагрузить устройство
        result['STATE'] = 0 # 0 - ничего не делать, 3 - заблокировать доступ

    # ----------------------
    # Составить карту ячеек,
    # положить в кэш
    # devices = {
    #   ID:{},
    # }
    # ----------------------
    cache_key_devices = "acon_devices"
    cache_key_commands = "acon_commands"

    if result.get("ID"):
      devices = redis_db.get(cache_key_devices)
      if not devices:
        devices = {}
      else:
        devices = pickle.loads(devices)
      devices[result['ID']] = result

      redis_db.set(cache_key_devices, pickle.dumps(devices, protocol=2))
    commands = redis_db.get(cache_key_commands)
    if commands:
      commands = pickle.loads(commands)
    else:
      commands = {}

    # Задание пароля
    if "numbers" in result:
        result['passwords'] = []
        test_box = 63
        if result['numbers']:
            # ------------------------------------
            # Из кэша берем команды для устройства
            # ------------------------------------
            device_commands = commands.get(result['ID'])
            if device_commands:
                for cmd in device_commands:
                    result['passwords'].append('BOX_ENG=({},{},{})'.format(cmd[0], cmd[1], cmd[2]))
          # ------------------------
          # Пример задания парольки
          # для предпоследней ячейки
          # ------------------------
          #if len(result['numbers']) >= test_box:
              #if result['numbers'][(test_box - 1)] == 1:
                  #result['passwords'].append(
                  #  'BOX_ENG=({},{},{})'.format(
                  #    (test_box - 1),
                  #    random.randint(1000,9999),
                  #    4800,
                  #  )
                  #)
    # ------------------------
    # Формируем конечный ответ
    # ------------------------
    if result:
        result['msg'] = """{title}
ID={ID}
SEND={SEND}
RESET={RESET}
STATE={STATE}{new_line}{BOX_ARR}
CLOSE
""".format(title = result['title'],
           ID = result['ID'],
           SEND = result['SEND'],
           RESET = result['RESET'],
           STATE = result['STATE'],
           new_line = "" if not result['passwords'] else "\n",
           BOX_ARR = "\n".join(result['passwords']), )

    return result

def handle_events(events, server):
    """События на входах"""
    for event in events:
        # ----------------------------
        # Событие от серверного сокета
        # => новое подключение
        # ----------------------------
        if event is server:
            conn, client_ip = event.accept()
            conn.setblocking(0)
            INPUTS.append(conn)
            now = datetime.datetime.today().strftime("%H:%M:%S %d-%m-%Y")
            logger.info("{date} new connection from {client_ip}\n".format(
                date = now,
                client_ip = client_ip,
              )
            )
        # Событие НЕ от серверного сокета
        else:
            data = ""
            try:
                data = event.recv(1024)
            except ConnectionResetError:
                logger.error("connection reset by peer")
            if data:
                cmd = b2str(data)
                logger.info("[RECEIVED]:\n{cmd}".format(cmd=cmd))
                if event not in OUTPUTS:
                    OUTPUTS.append(event)
                # Команды
                # https://docs.python.org/3/library/socket.html#socket-objects
                # Печатаем адрес сервера и адрес клиента
                if cmd in ("ip", "server", "client", "info"):
                  try:
                      event.send(bytes("{}=>{}\r\n".format(
                          event.getpeername(),
                          event.getsockname(), ), encoding="UTF-8")
                      )
                  except:
                      clear_event(event)
                  return
                # Команда "Выход"
                if cmd in ("exit", "quit"):
                    clear_event(event)

                # Специальная пакетная команда
                try:
                    result = special_command(cmd)
                except Exception as e:
                    result = {"msg":"ERROR: {}".format(e)}
                    logger.error(traceback.print_exc(file=sys.stdout))
                if result:
                    try:
                        event.send(bytes(result['msg'], encoding="UTF-8"))
                        logger.info("[RESPONSE]:\n{}".format(result['msg']))
                    except:
                        clear_event(event)
                    return

                #try:
                    #event.send(bytes("[RESPONSE]:{}\r\n".format(cmd), encoding="UTF-8"))
                #except:
                    #clear_event(event)
                clear_event(event)
            else:
                # Данных нет, но событие сработало
                clear_event(event)

def clear_event(event):
    """Очистка ресурсов использования сокета"""
    if event in OUTPUTS:
        OUTPUTS.remove(event)
    if event in INPUTS:
        INPUTS.remove(event)
    event.close()
    logger.info("closing connection " + str(event))

# Основной цикл программы
if __name__ == "__main__":
    server_socket = get_non_blocking_server_socket()
    INPUTS.append(server_socket)
    logger.info("server is running {}, ctrl+c to stop".format(SERVER_ADDRESS))
    try:
        while INPUTS:
            r, w, e = select.select(INPUTS, OUTPUTS, INPUTS)
            handle_events(r, server_socket)
    except KeyboardInterrupt:
        clear_event(server_socket)
        logger.error("server stopped")
