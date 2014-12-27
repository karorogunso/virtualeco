#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
import socket
import traceback
import threading
import struct
from lib import env
from lib import general
from lib.packet.launch_data_handler import LaunchDataHandler
from lib.packet.login_data_handler import LoginDataHandler
from lib.packet.map_data_handler import MapDataHandler
from lib.site_packages import rijndael
from lib.packet.packet_struct import *
USE_NULL_KEY = False #emergency option
if USE_NULL_KEY:
	GENERATOR = 1
	PRIME = 0
	PRIVATE_KEY = 0
	PUBLIC_KEY = 0
	PRIME_BYTES = "\x00"*0x100
	PUBLIC_KEY_BYTES = "\x00"*0x100
else:
	#get key info (server private key / public key)
	GENERATOR = 3
	PRIME = general.get_prime()
	PRIVATE_KEY = general.get_private_key()
	PUBLIC_KEY = general.get_public_key(GENERATOR, PRIVATE_KEY, PRIME)
	#get bytes
	PRIME_BYTES = general.int_to_bytes(PRIME)
	PUBLIC_KEY_BYTES = general.int_to_bytes(PUBLIC_KEY)
	#general.log("prime:", PRIME_BYTES, "\nlength:", len(PRIME_BYTES))
	#general.log("public key:", PUBLIC_KEY_BYTES, "\nlength:", len(PUBLIC_KEY_BYTES))
	#get key exchange packet
PACKET_KEY_EXCHANGE = "".join((
	pack_int(0), #head
	pack_int(len(str(GENERATOR)))+str(GENERATOR), #generator
	pack_int(len(PRIME_BYTES))+PRIME_BYTES, #prime #std len: 0x100
	pack_int(len(PUBLIC_KEY_BYTES))+PUBLIC_KEY_BYTES #server public key #std len: 0x100
))
PACKET_INIT = "\x00\x00\x00\x00\x00\x00\x00\x10"
PACKET_INIT_LENGTH = len(PACKET_INIT)
PACKET_NULL_KEY = "\x00\x00\x00\x01\x30"
VALUE_NULL_KEY = 0

class StandardServer(threading.Thread):
	def __init__(self, addr, client_class):
		threading.Thread.__init__(self)
		self.setDaemon(True)
		self.bind_addr = addr
		self.running = True
		self.client_list = []
		self.client_list_lock = threading.RLock()
		self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.socket.bind(addr)
		self.socket.listen(10)
		self.client_class = client_class
		self.start()
	
	def _shutdown(self):
		if not self.running:
			return
		general.log("[ srv ] shutdown", self)
		self.running = False
		self.socket.close()
		while self.client_list:
			client = self.client_list[0]
			general.log("[ srv ] shutdown", client)
			client.stop() #remove in StandardClient._stop
		#cannot join when socket blocking
	
	def ip_count_check(self, src):
		ip = src[0]
		ip_count = 1
		with self.client_list_lock:
			for client in self.client_list:
				if ip == client.src_address[0]:
					ip_count += 1
		general.log("[ srv ] src: %s ip_count: %s"%(src, ip_count))
		if ip_count > env.MAX_CONNECTION_FROM_ONE_IP:
			return False
		else:
			return True
	
	def run(self):
		while self.running:
			try:
				s, src = self.socket.accept()
				if not self.ip_count_check(src):
					s.close()
					continue
				with self.client_list_lock:
					self.client_list.append(self.client_class(self, s, src))
			except:
				general.log_error(traceback.format_exc())

class StandardClient(threading.Thread):
	def __init__(self, master, s, src):
		threading.Thread.__init__(self)
		self.setDaemon(True)
		self.master = master
		self.socket = s
		self.src_address = src
		self.buf = ""
		self.running = True
		self.rijndael_key = None
		self.rijndael_obj = None
		self.send_lock = threading.RLock()
		self.start()
	
	def __str__(self):
		return "%s<%s:%s>"%(repr(self), self.src_address[0], self.src_address[1])
	
	def recv_packet(self, length):
		try:
			data = self.socket.recv(length)
		except EOFError:
			raise EOFError("socket closed")
		except:
			raise EOFError(traceback.format_exc())
		if not self.running:
			raise EOFError("not self.running")
		if not data:
			raise EOFError("not data")
		return data
	
	def recv_packet_force(self, length):
		data = ""
		while len(data) < length:
			data += self.recv_packet(length-len(data))
		return data
	
	def recv_key_packet(self):
		return self.recv_packet_force(
			unpack_unsigned_int(self.recv_packet_force(4))
		)
	
	def recv_enc_packet(self):
		return self.recv_packet_force(
			unpack_unsigned_int(self.recv_packet_force(4))+4
		)
	
	def run(self):
		try:
			self.recv_init()
			self.recv_key()
			while self.running:
				self.handle_packet()
		except EOFError:
			self.stop()
		except:
			general.log_error(traceback.format_exc())
			self.stop()
		general.log("[ srv ] quit", self)
	
	def send_packet(self, packet):
		#general.log("[ srv ] send", packet.encode("hex"))
		with self.send_lock:
			self.socket.sendall(packet)
	
	def recv_init(self):
		packet = self.recv_packet_force(PACKET_INIT_LENGTH)
		if packet != PACKET_INIT:
			raise ValueError("packet != PACKET_INIT")
		self.send_packet(PACKET_KEY_EXCHANGE)
	
	def recv_key(self):
		#get client public key
		client_public_key_bytes = self.recv_key_packet()
		client_public_key = general.bytes_to_int(client_public_key_bytes)
		#general.log("[ srv ] client key:", client_public_key_bytes)
		#general.log("[ srv ] length:", len(client_public_key_bytes))
		self.recv_key = True
		if USE_NULL_KEY:
			if client_public_key != VALUE_NULL_KEY:
				raise ValueError("client_public_key != VALUE_NULL_KEY")
			self.rijndael_key = "\x00"*0x10
		else:
			#get share key
			share_key_bytes = general.get_share_key_bytes(
				client_public_key, PRIVATE_KEY, PRIME)
			general.log("[ srv ] share key:", share_key_bytes)
			#general.log("[ srv ] length:", len(share_key_bytes))
			#get rijndael key (str)
			self.rijndael_key = general.get_rijndael_key(share_key_bytes)
			self.rijndael_obj = rijndael.rijndael(
				self.rijndael_key, block_size=16
			)
			self.rijndael_obj.lock = threading.RLock()
		general.log("[ srv ] rijndael key:", self.rijndael_key.encode("hex"))
	
	def handle_packet(self):
		packet = self.recv_enc_packet()
		try:
			self.handle_data(general.decode(packet, self.rijndael_obj))
		except:
			general.log_error(traceback.format_exc())
	
	def _stop(self):
		if not self.running:
			return
		self.socket.close()
		self.running = False
		with self.master.client_list_lock:
			self.master.client_list.remove(self)

class LaunchClient(StandardClient, LaunchDataHandler):
	def __init__(self, *args):
		general.log("[ srv ] launch client", args)
		LaunchDataHandler.__init__(self)
		StandardClient.__init__(self, *args)

class LoginClient(StandardClient, LoginDataHandler):
	def __init__(self, *args):
		general.log("[ srv ] login client", args)
		LoginDataHandler.__init__(self)
		StandardClient.__init__(self, *args)

class MapClient(StandardClient, MapDataHandler):
	def __init__(self, *args):
		general.log("[ srv ] map client", args)
		MapDataHandler.__init__(self)
		StandardClient.__init__(self, *args)

class LaunchServer(StandardServer):
	def __init__(self, addr):
		StandardServer.__init__(self, addr, LaunchClient)

class LoginServer(StandardServer):
	def __init__(self, addr):
		StandardServer.__init__(self, addr, LoginClient)

class MapServer(StandardServer):
	def __init__(self, addr):
		StandardServer.__init__(self, addr, MapClient)

def init():
	if os.name == "nt":
		assert_address_not_used()

def assert_address_not_used():
	general.log_line("[ srv ] server.assert_address_not_used ... ")
	general.assert_address_not_used((env.SERVER_BROADCAST_ADDR, env.LAUNCH_SERVER_PORT))
	general.assert_address_not_used((env.SERVER_BROADCAST_ADDR, env.LOGIN_SERVER_PORT))
	general.assert_address_not_used((env.SERVER_BROADCAST_ADDR, env.MAP_SERVER_PORT))
	general.assert_address_not_used((env.SERVER_BROADCAST_ADDR, env.WEB_SERVER_PORT))
	general.log("done.")

def load():
	global launchserver
	global loginserver
	global mapserver
	launchserver_bind_addr = (env.SERVER_BIND_ADDR, env.LAUNCH_SERVER_PORT)
	loginserver_bind_addr = (env.SERVER_BIND_ADDR, env.LOGIN_SERVER_PORT)
	mapserver_bind_addr = (env.SERVER_BIND_ADDR, env.MAP_SERVER_PORT)
	general.log("[ srv ] start launch server with\t%s:%d"%launchserver_bind_addr)
	launchserver = LaunchServer(launchserver_bind_addr)
	general.log("[ srv ] start login server with\t%s:%d"%loginserver_bind_addr)
	loginserver = LoginServer(loginserver_bind_addr)
	general.log("[ srv ] start map server with\t%s:%d"%mapserver_bind_addr)
	mapserver = MapServer(mapserver_bind_addr)
