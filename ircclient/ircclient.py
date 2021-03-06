from __future__ import with_statement
import sys
import socket
import re
import time
import datetime
import errno
import random
import string
import settings
import ssl

from autoreloader.autoreloader import AutoReloader

def timestamp():
	return datetime.datetime.now().strftime("[%H:%M:%S]")

class IRCClient(AutoReloader):
	def __init__(self, address, port, nick, username, realname, network, password):
		self.connected = False
		self.active_session = False
		self.temp_nick_list_channel = None
		self.temp_nick_list = None
		self.nick_lists = {}
		self.recv_buf = ''
		self.callbacks = {}

		self.lines = []

		self.s = None

		self.wait_until = None

		self.irc_message_pattern = re.compile('^(:([^  ]+))?[   ]*([^  ]+)[  ]+:?([^  ]*)[   ]*:?(.*)$')
		self.message_handlers = {
			'JOIN': self.on_join,
			'KICK': self.on_kick,
			'NICK': self.on_nick,
			'PART': self.on_part,
			'QUIT': self.on_quit,
			'PING': self.on_ping,
			'PRIVMSG': self.on_privmsg,
			'NOTICE': self.on_notice,
			'ERROR': self.on_error,
			'353': self.on_begin_nick_list,
			'366': self.on_end_nick_list,
			'001': self.on_connected,
			'433': self.on_nick_inuse,
		}

		self.server_address = address;
		self.server_port = port;

		self.nick = nick
		self.username = username
		self.realname = realname
		self.network = network
		self.password = password

	def connect(self, address, port):
		self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

		self.active_session = False
		self.ping_count = 0

		try:
			if settings.Settings().networks[self.network].setdefault("ssl", False):
				self.s = ssl.wrap_socket(self.s)
		except Exception, ex:
			print timestamp() + " " + self.network + " Connection with SSL failed, " + str(ex)

		try:
			self.s.connect((address, port))
			self.connected = True
		except Exception, ex:
			print timestamp() + " " + self.network + " Connect failed, " + str(ex)
			self.connected = False

		if self.connected:
			self.s.setblocking(False)

		return self.connected

	def log_line(self, line):
		try:
			print line
		except UnicodeEncodeError:
			# FIXME use bot.settings/rebuild settings
			print line.encode(settings.Settings().recode_fallback, "replace")
		self.lines.append(line)
	
	def send(self, line):
		self.log_line(timestamp() + " " + self.network + " SENT: " + line)

		data = line + "\r\n"

		while data:
			# FIXME use bot.settings/rebuild settings
			try:
				sent =  self.s.send(data.encode(settings.Settings().recode_out_default_charset))
			except UnicodeDecodeError:
				# String is probably not unicode, print warning and just send it
				print
				print "WARNING IRCClient send called with non unicode string, fix this!"
				print
				sent = self.s.send(data)
			except UnicodeEncodeError:
				# Try fallback coding instead
				sent =  self.s.send(data.encode(settings.Settings().recode_fallback, "ignore"))

			data = data[sent:]

		return len(line)+2

	def is_connected(self):
		return self.connected

	def tell(self, target, string):
		if len(string) >= 399:
			string = string[0:399]

		split = len(string) - 1

		if split >= 400:
			split = 400
			while split > 350:
				if string[split] == ' ':
					break
				split -= 1

			a = string[0:split]
			b = string[split:]
		
			return self.tell(target, a) + self.tell(target, b)
		else:
			return self.send("PRIVMSG " + target + " :" + string)

	def join(self, channel, password=""):
		return self.send('JOIN ' + channel + ' ' + password)

	def get_nick(self, host):
		m = re.search('^:?(\S+?)!', host)
		if m:
			return m.group(1)
		else:
			return host

	def on_begin_nick_list(self, tupels):
		m = re.search('. (.+?) :(.*)$', tupels[5])

		if m:
			channel, nicks = m.group(1, 2)

			if self.temp_nick_list_channel != channel:
				self.temp_nick_list_channel = channel
				self.temp_nick_list = []

			for m in re.findall('([^a-zA-Z\[\]{}]?)(.+?)(\s|$)', nicks):
				prefix, nick = m[0:2]

				self.temp_nick_list.append(nick)
			
	def on_end_nick_list(self, tupels):
		self.nick_lists[self.temp_nick_list_channel] = self.temp_nick_list
		self.temp_nick_list_channel = None
		self.temp_nick_list = None

	def on_join(self, tupels):
		source, channel = [tupels[1], tupels[4]]

		if "on_join" in self.callbacks:
			self.callbacks["on_join"](self.network, source, channel)

	def on_kick(self, tupels):
		source, channel = [tupels[1], tupels[4]]
		target_nick = None

		m = re.search('^([^ ]+)', tupels[5])
		if m:
			target_nick = m.group(1)

		if "on_kick" in self.callbacks:
			self.callbacks["on_kick"](self.network, source, channel, target_nick)

		if target_nick:
			for nick_list in self.nick_lists.values():
				if target_nick in nick_list:
					nick_list.remove(target_nick)

	def on_nick(self, tupels):
		source, new_nick = [tupels[1], tupels[4]]

		if "on_nick_change" in self.callbacks:
			self.callbacks["on_nick_change"](self.network, source, new_nick)

		source_nick = self.get_nick(source)

		for nick_list in self.nick_lists.values():
			if source_nick in nick_list:
				nick_list.remove(source_nick)
				nick_list.append(new_nick)

	def on_nick_inuse(self, tuples):
		self.send("NICK " + self.nick + "_" + "".join([random.choice(string.ascii_letters + 
			  string.digits + ".-") for i in xrange(3)]))

	def on_part(self, tupels):
		source, channel, reason = [tupels[1], tupels[4], tupels[5]]

		if "on_part" in self.callbacks:
			self.callbacks["on_part"](self.network, source, channel, reason)

		source_nick = self.get_nick(source)

		for nick_list in self.nick_lists.values():
			if source_nick in nick_list:
				nick_list.remove(source_nick)

	def on_quit(self, tupels):
		source = tupels[1]
		reason = tupels[4]

		if tupels[5]:
			reason += ' ' + tupels[5]

		source_nick = self.get_nick(source)

		if "on_quit" in self.callbacks:
			self.callbacks["on_quit"](self.network, source_nick, reason)

		for nick_list in self.nick_lists.values():
			if source_nick in nick_list:
				nick_list.remove(source_nick)

	def on_ping(self, tupels):
		self.ping_count += 1
		self.send("PONG :" + tupels[4])

	def on_privmsg(self, tupels):
		source, target, message = tupels[2], tupels[4], tupels[5]

		if target[0] != '#':
			target = source

		if "on_privmsg" in self.callbacks:
			self.callbacks["on_privmsg"](self.network, source, target, message)

	def on_notice(self, tupels):
		source, target, message = tupels[2], tupels[4], tupels[5]

		if target[0] != '#':
			target = source

		if "on_notice" in self.callbacks:
			self.callbacks["on_notice"](self.network, source, target, message)

	def on_connected(self, tupels):
		self.active_session = True

		if "on_connected" in self.callbacks:
			self.callbacks["on_connected"](self.network)

	def on_error(self, tupels):
		message = tupels[5]
		print 'the irc server informs of an error:', message

		if "host is trying to (re)connect too fast" in message:
			self.idle_for(120)

	def idle_for(self, seconds):
		self.wait_until = datetime.datetime.now() + datetime.timedelta(0, seconds)

	def tick(self):
		if self.wait_until and self.wait_until > datetime.datetime.now():
			return

		if self.connected:
			try:
				retn = self.s.recv(1024)

				self.recv_buf += retn
				recv_lines = self.recv_buf.splitlines(True)
				self.recv_buf = ''
				for line in recv_lines:
					if not line.endswith("\r\n"):
						self.recv_buf = line
					else:
						line = line.rstrip("\r\n")
						self.log_line(timestamp() + " " + self.network + " RECV: " + line)
						m = self.irc_message_pattern.match(line)
						if m:
							if m.group(3) in self.message_handlers:
								self.message_handlers[m.group(3)](m.group(0, 1, 2, 3, 4, 5))

			except ssl.SSLError, (error_code, error_message):
				if error_code != errno.EWOULDBLOCK and error_code != errno.ENOENT:
					self.connected = False
					print (error_code, error_message)
			except socket.error, (error_code, error_message):
				if error_code != errno.EWOULDBLOCK:
					self.connected = False
					print (error_code, error_message)
		else:
			try:
				self.connect(self.server_address, self.server_port)
			except socket.error, (error_code, error_message):
				print "I got an error while trying to connect... Is it wrong to just return now?", (error_code, error_message)
				self.idle_for(60)
				return

			if self.connected:
				if self.password is not None:
					self.send("PASS %s" % self.password)
				self.send("USER %s * * :%s" % (self.username, self.realname))
				self.send("NICK %s" % self.nick)
