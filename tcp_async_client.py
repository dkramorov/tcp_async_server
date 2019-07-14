#!/usr/bin/env python
#-*-coding:utf-8-*-
import sys
import socket
import logging
from envparse import env

logger = logging.getLogger(__name__)

env.read_envfile()
LOG_LEVEL = env('LOG_LEVEL', default='INFO')
SERVER_HOST = env('SERVER_HOST', default='localhost')
SERVER_PORT = env('SERVER_PORT', default=5000, cast=int)
MAX_CONNECTIONS = env('MAX_CONNECTIONS', default=10, cast=int)

logger.setLevel(LOG_LEVEL)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(sh)

server = (SERVER_HOST, SERVER_PORT)

msg = """ACon_V1.0
ID=1953
SEND=141349
CONNECTION=1
WIFI_STATE=0
GSM_STATE=0
RESET=1
STATE=0
COLS=8
ROWS=8
ACT_CON=01011011,11111111,10110111,11111111,10011111,11111111,10110111,11111111
BOX_ENG=2,5,8,23,24,25
BOX_TO=33,34
BOX_ACC=1,54,43
CLOSE"""

def test_parallel_connections():
  clients = [
    socket.socket(
      socket.AF_INET,
      socket.SOCK_STREAM
    ) for i in range(MAX_CONNECTIONS)
  ]
  for client in clients:
    client.connect(server)

  for i in range(MAX_CONNECTIONS):
    clients[i].send(bytes(msg.replace("\n", "\r\n"), encoding="UTF-8"))

  for client in clients:
    data = client.recv(1024)
    logger.info(f'\n{data.decode("utf-8")}')

def test_single_connection():
    client = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )
    client.connect(server)
    client.send(bytes(msg.replace("\n", "\r\n"), encoding="UTF-8"))
    data = client.recv(1024)
    logger.info(f'\n{data.decode("utf-8")}')

logging.info("TEST SINGLE CONNECTION")
test_single_connection()
logging.info("TEST MULTIPLE CONNECTIONS")
test_parallel_connections()
