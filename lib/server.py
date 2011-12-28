#!/usr/bin/env python
# -*- coding: utf-8 -*-
import socket
import thread
import threading
import traceback
import struct
from lib import general
from lib.packet.login_data_handle import LoginDataHandle
from lib.packet.map_data_handle import MapDataHandle
KEY_HEAD = "\x00\x00\x00\x00\x00\x00\x00\x01\x31"
KEY_PRIME = "\x00\x00\x01\x00"+"\x00"*0x100
KEY_PUBLIC = "\x00\x00\x01\x00"+"\x00"*0x100
PACKET_INIT = "\x00\x00\x00\x00\x00\x00\x00\x10"
PACKET_KEY = "\x00\x00\x00\x01\x30"
BIND_ADDRESS = "0.0.0.0"

class StandardServer(threading.Thread):
	def __init__(self, port):
		threading.Thread.__init__(self)
		self.setDaemon(True)
		self.port = port
		self.client_list = []
		self.lock = threading.RLock()
		self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.socket.bind((BIND_ADDRESS, port))
		self.socket.listen(10)
		self.start()
	def run(self):
		while True:
			try:
				s = self.socket.accept()
				with self.lock:
					self.handle_client(s)
			except:
				print traceback.format_exc()
class StandardClient(threading.Thread):
	def __init__(self, master, socket, address):
		threading.Thread.__init__(self)
		self.setDaemon(True)
		self.lock = threading.RLock()
		self.master = master
		self.address = address
		self.socket = socket
		self.buf = ""
		self.running = True
		self.recv_init = False
		self.recv_key = False
		self.start()
	def run(self):
		while self.running:
			try:
				packet = self.socket.recv(1024)
				if not self.running:
					return
				if not packet:
					raise Exception
				try:
					self.buf += packet
					with self.lock:
						self.handle_packet()
				except:
					print traceback.format_exc()
			except:
				self.stop()
	def send(self, data):
		with self.lock:
			self.socket.sendall(data)
	def handle_packet(self):
		if not self.recv_init:
			if self.buf.startswith(PACKET_INIT):
				self.recv_init = True
				self.buf = self.buf[len(PACKET_INIT):]
				self.send(KEY_HEAD+KEY_PRIME+KEY_PUBLIC)
			else:
				self.stop()
		elif not self.recv_key:
			if self.buf.startswith(PACKET_KEY):
				self.recv_key = True
				self.buf = self.buf[len(PACKET_KEY):]
			else:
				self.stop()
		else:
			#00000010 0000000c 6677bcf44144b39e28281ae8777db574
			packet_length = general.unpack_int(self.buf[:4])+8
			if packet_length <= len(self.buf):
				packet = self.buf[:packet_length]
				self.buf = self.buf[packet_length:]
			else:
				print "packet decode error:", self.buf.encode("hex")
				self.stop()
				return
			self.handle_data(general.decode(packet))
	def stop(self):
		if not self.running:
			return
		self.running = False
		with self.lock:
			self.socket.close()
			self.running = False
			with self.master.lock:
				self.master.client_list.remove(self)
		print "stop", self.address
		del self

class LoginServer(StandardServer):
	def handle_client(self, s):
		self.client_list.append(LoginClient(self, *s))
class MapServer(StandardServer):
	def handle_client(self, s):
		self.client_list.append(MapClient(self, *s))
class LoginClient(StandardClient, LoginDataHandle):
	def __init__(self, *args):
		print "new login client", args
		StandardClient.__init__(self, *args)
		LoginDataHandle.__init__(self)
class MapClient(StandardClient, MapDataHandle):
	def __init__(self, *args):
		print "new map client", args
		StandardClient.__init__(self, *args)
		MapDataHandle.__init__(self)

def load(path):
	from lib.obj import serverconfig
	global config
	config = serverconfig.ServerConfig(path)
	global loginserver
	loginserver = LoginServer(config.loginserverport)
	print "Start login server with\t%s:%d"%(BIND_ADDRESS, config.loginserverport)
	global mapserver
	mapserver = MapServer(config.mapserverport)
	print "Start map server with\t%s:%d"%(BIND_ADDRESS, config.loginserverport)
