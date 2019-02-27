#!/usr/bin/env python3 -u

"""
Support for DSC alarm control panels using IT-100 integration module by emulating an EnvisaLink EVL-4
"""

import logging
import sys
import itertools
import os
import time
import multiprocessing
import subprocess
import signal
import serial
import inspect
import random
import socket

REQUIREMENTS = ['pyserial']

_LOGGER = logging.getLogger(__name__)

DEFAULT_PARTITIONS = 1
DEFAULT_ZONES = 64

#SERIAL_PORT = '/dev/it100'
SERIAL_PORT = '/dev/ttyUSB0'
SERIAL_BAUD = 9600
NETWORK_HOST = '0.0.0.0'
NETWORK_PORT = 4025

HEX_MSG = True


# Zone state definitions
ZONE_OPEN = 0
ZONE_CLOSED = 1


# --------------------------------------------------------------------------------
#	DSC Protcol definitions
# --------------------------------------------------------------------------------
COMMAND_POLL = '000'
COMMAND_STATUS_REQUEST = '001'
COMMAND_LABELS_REQUEST = '002'
COMMAND_SET_TIME_DATE = '010'
COMMAND_OUTPUT_CONTROL = '020'
COMMAND_PARTITION_ARM_CONTROL_AWAY = '030'
COMMAND_PARTITION_ARM_CONTROL_STAY = '031'
COMMAND_PARTITION_ARM_CONTROL_ZERO_ENTRY = '032'
COMMAND_PARTITION_ARM_CONTROL_WITH_CODE = '033'
COMMAND_PARTITION_DISARM_CONTROL = '040'
COMMAND_TIME_STAMP_CONTROL = '055'
COMMAND_TIME_DATE_BCAST_CONTROL = '056'
COMMAND_TEMPERATURE_BCAST_CONTROL = '057'
COMMAND_VIRTUAL_KEYBOARD_CONTROL = '058'
COMMAND_TRIGGER_PANIC_ALARM = '060'
COMMAND_KEY_PRESSED = '070'
COMMAND_SET_BAUD_RATE = '080'
COMMAND_CODE_SEND = '200'

NOTIFY_ACK = '500'
NOTIFY_ERROR = '501'
NOTIFY_SYSTEM_ERROR = '502'
NOTIFY_TIME_DATE_BCAST = '550'
NOTIFY_LABELS = '570'
NOTIFY_BAUD_RATE_SET = '580'
NOTIFY_ZONE_ALARM = '601'
NOTIFY_ZONE_ALARM_RESTORE = '602'
NOTIFY_ZONE_TAMPER = '603'
NOTIFY_ZONE_TAMPER_RESTORE = '604'
NOTIFY_ZONE_FAULT = '605'
NOTIFY_ZONE_FAULT_RESTORE = '606'
NOTIFY_ZONE_OPEN = '609'
NOTIFY_ZONE_RESTORED = '610'
NOTIFY_DURESS_ALARM = '620'
NOTIFY_FIRE_KEY_ALARM = '621'
NOTIFY_FIRE_KEY_RESTORED = '622'
NOTIFY_AUXILARY_KEY_ALARM = '623'
NOTIFY_AUXILARY_KEY_RESTORED = '624'
NOTIFY_PANIC_KEY_ALARM = '625'
NOTIFY_PANIC_KEY_RESTORED = '626'
NOTIFY_AUXILARY_INPUT_ALARM = '631'
NOTIFY_AUXILARY_INPUT_RESTORED = '632'
NOTIFY_PARTITION_READY = '650'
NOTIFY_PARTITION_NOT_READY = '651'
NOTIFY_PARTITION_ARMED = '652'
NOTIFY_PARTITION_READY_TO_FORCE_ARM = '653'
NOTIFY_PARTITION_IN_ALARM = '654'
NOTIFY_PARTITION_DISARMED = '655'
NOTIFY_PARTITION_EXIT_DELAY = '656'
NOTIFY_PARTITION_ENTRY_DELAY = '657'
NOTIFY_KEYPAD_LOCKOUT = '658'
NOTIFY_KEYPAD_BLANKING = '659'
NOTIFY_COMMAND_OUTPUT = '660'
NOTIFY_INVALID_CODE = '670'
NOTIFY_FUNCTION_NOT_AVAILABLE = '671'
NOTIFY_FAILED_TO_ARM = '672'
NOTIFY_PARTITION_BUSY = '673'
NOTIFY_PARTITION_USER_CLOSING = '700'
NOTIFY_PARTITION_SPECIAL_CLOSING = '701'
NOTIFY_PARTITION_PARTIAL_CLOSING = '702'
NOTIFY_PARTITION_USER_OPENING = '750'
NOTIFY_PARTITION_SPECIAL_OPENING = '751'
NOTIFY_PANEL_BATTERY_TROUBLE = '800'
NOTIFY_PANEL_BATTERY_RESTORED = '801'
NOTIFY_PANEL_AC_TROUBLE = '802'
NOTIFY_PANEL_AC_RESTORED = '803'
NOTIFY_SYSTEM_BELL_TROUBLE = '806'
NOTIFY_SYSTEM_BELL_RESTORED = '807'
NOTIFY_GENERAL_DEV_LOW_BATTERY = '821'
NOTIFY_GENERAL_DEV_LOW_BATTERY_RESTORED = '822'
NOTIFY_GENERAL_SYSTEM_TAMPER = '829'
NOTIFY_GENERAL_SYSTEM_TAMPER_RESTORED = '830'
NOTIFY_PARTITION_TROUBLE = '840'
NOTIFY_PARTITION_TROUBLE_RESTORED = '841'
NOTIFY_FIRE_TROUBLE_ALARM = '842'
NOTIFY_FIRE_TROUBLE_RESTORED = '843'
NOTIFY_KEYBUS_FAULT = '896'
NOTIFY_KEYBUS_RESTORED = '897'
NOTIFY_CODE_REQUIRED = '900'
NOTIFY_BEEP_STATUS = '904'
NOTIFY_VERSION = '908'


# --------------------------------------------------------------------------------
#	Envisalink Protcol definitions
# --------------------------------------------------------------------------------
EVL_LOGIN_REQUEST = '005'
EVL_DUMP_TIMERS = '008'
EVL_KEY_STRING = '071'

EVL_LOGIN_INTERACTION = '505'
EVL_DUMP_TIMER_RESPONSE = '615'

"""
Command codes not handled by DSC
005
008
071
072
073
074
80 ??

Response codes not handled by DSC
505
510
511
615
616
663
664
674
680
815
849
912
921
922
"""

# --------------------------------------------------------------------------------
#	Classes
# --------------------------------------------------------------------------------


class dsc_zone():
	def __init__(self, zone):
		self.zone = zone
		self.state = ZONE_CLOSED
		self.description = ""
		self.close_time = 0

	def getZone(self):
		return self.zone

	def setState(self, newstate):
		# Update zone timer if zone is going from open to closed
		if (self.state == ZONE_OPEN and newstate == ZONE_CLOSED):
			self.close_time = time.time()
		# Set new state
		self.state = newstate
			
	def getState(self):
		return self.state

	def setDescription(self, desc):
		self.description = desc
	def getDescription(self):
		return self.description

	"""
    Report zone timer as 4-byte little-endian string
      FFFF = open
      When closed, start counting down from 0xFFFF every five seconds
      eg. after ten seconds the value is 0xFFFD, return little-endian as FDFF

      Implementation note:
      If HA polls the zone timers within five seconds of closing and sees 0xFFFF because it hasn't decremented yet, it assumes the zone has re-opened.
      None of the documentation mentions this.

      Any result less than 30 seconds is treated as still open (WTF?)
      https://community.home-assistant.io/t/dsc-alarm-integration/409/390
      It's in pyenvisalink/envisalink_base_client.py
      
      Solution is to start counting down from 0xFFFA
	"""
	def getTimer(self):
		if (self.state == ZONE_OPEN):
			return "FFFF"
		else:
			timedelta = int((time.time() - self.close_time) / 5)
			timedeltastring = format(max(0, 0xFFFF - 6 - timedelta), '04X')
			timedeltastringLE = timedeltastring[2:4] + timedeltastring[0:2]
			return timedeltastringLE

		





# --------------------------------------------------------------------------------
#	Serial I/O Routines
# --------------------------------------------------------------------------------

"""
	Listen to serial port and put incoming messages into the read queue
	- Only passes message content.  Checksum and CR/LF are removed.
"""
def serialRead(readQueueSer, port):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		lastdatatime = time.time()
		msgbuf = ''
		
		while True:
			read_byte = port.read(1)

			# If there is a long delay between messages while data is in the buffer, assume something went wrong and throw out the old data
			thisdatatime = time.time()
			if ( ((thisdatatime - lastdatatime) > 0.5) and (msgbuf.__len__() > 0) ):
				print ("ERROR: flushing stale data from receive buffer {}".format(msgbuf))
				msgbuf=''

			# Add each byte of received data to message buffer			
			msgbuf += read_byte.decode('ASCII')
			lastdatatime = time.time()

			# If we have enough characters for a full message, start checking for CR/LR terminator
			if (msgbuf.__len__() >= 7):
				if (ord(msgbuf[msgbuf.__len__()-2]) == 0x0D and ord(msgbuf[msgbuf.__len__()-1]) == 0x0A):
					# Found terminator, message is complete.
					if (HEX_MSG):
						timestamp=time.strftime("[%H:%M:%S]", time.localtime())
						print ("{} DSC In  > {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msgbuf), msgbuf[0:msgbuf.__len__() - 2] ))
					# Queue message if checksum OK
					msgdata = msgbuf[0:msgbuf.__len__() - 4]
					msgchksum = msgbuf[msgbuf.__len__() - 4:msgbuf.__len__() - 2]
					if (msgchksum == dsc_checksum(msgdata)):
						readQueueSer.put(msgdata)
					else:
						print ("{} DSC In  > Checksum error".format(timestamp))
					msgbuf = ''

	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None



"""
	Pull messages from write queue and send them to the serial port
	- Does not add checksum or CR/LF
"""
def serialWrite(writeQueueSer, port):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:
			# Block until something is in the queue
			msg = writeQueueSer.get(True, None)

			# Send message
			if (HEX_MSG):
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				print("{} DSC Out < {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msg), msg[0:msg.__len__() - 2] ))
			port.write(bytes(msg, 'UTF-8'))

	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None



# --------------------------------------------------------------------------------
#	Network Functions
# --------------------------------------------------------------------------------

"""
	Listen to socket connection and put incoming messages into the read queue
	This routine does not handle the actual connection, just interacting with the existing connection
	- Only passes message content.  Checksum and CR/LF are removed.
"""
def networkRead(readQueueNet, conn):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))
	
	try:
		lastdatatime = time.time()
		msgbuf = ''
		
		while True:
			read_byte = conn.recv(1)

			if (read_byte == b''):
				raise NameError('Connection closed by client')

			# If there is a long delay between messages while data is in the buffer, assume something went wrong and throw out the old data
			thisdatatime = time.time()
			if ( ((thisdatatime - lastdatatime) > 0.5) and (msgbuf.__len__() > 0) ):
				print ("ERROR: flushing stale data from receive buffer {}".format(msgbuf))
				msgbuf=''

			# Add each byte of received data to message buffer			
			msgbuf += read_byte.decode('ASCII')
			lastdatatime = time.time()

			# If we have enough characters for a full message, start checking for CR/LR terminator
			if (msgbuf.__len__() >= 7):
				if (ord(msgbuf[msgbuf.__len__()-2]) == 0x0D and ord(msgbuf[msgbuf.__len__()-1]) == 0x0A):
					# Found terminator, message is complete.
					if (HEX_MSG):
						timestamp=time.strftime("[%H:%M:%S]", time.localtime())
						print ("{} EVL In  > {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msgbuf), msgbuf[0:msgbuf.__len__() - 2] ))
					# Queue message if checksum OK
					msgdata = msgbuf[0:msgbuf.__len__() - 4]
					msgchksum = msgbuf[msgbuf.__len__() - 4:msgbuf.__len__() - 2]
					if (msgchksum == dsc_checksum(msgdata)):
						readQueueNet.put(msgdata)
					else:
						print ("{} EVL In  > Checksum error".format(timestamp))
					msgbuf = ''

	except NameError:
		print ("Connection closed by client, terminating networkRead thread.")
		conn.close()
		return None
	except OSError:
		print ("OSError: {}".format(inspect.stack()[0][3]))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None


"""
	Pull messages from write queue and send them to the socket connection
	- Does not add checksum or CR/LF
"""
def networkWrite(writeQueueNet, conn):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:
			# This should block until something is in the queue
			msg = writeQueueNet.get(True, None)

			# Print message and send it out the socket connection
			if (HEX_MSG):
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				print("{} EVL Out < {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msg), msg[0:msg.__len__() - 2] ))
			conn.send(bytes(msg, 'UTF-8'))

	except OSError:
		print ("OSError: {}".format(inspect.stack()[0][3]))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None


"""
	Test function
	Open a network socket to accept fake commands that look like they're originating from the panel
	Note: Don't send checksum or CR/LF, they're not needed.
"""
def networkReadTest(readQueueSer):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		sock = socket.socket()
		sock.bind((NETWORK_HOST, (1 + NETWORK_PORT)))
		sock.setblocking(1)
		sock.listen(5)
	
		print ("Test listening on {}:{}".format(NETWORK_HOST, str(1 + NETWORK_PORT)))
		while True:
			conn, addr = sock.accept()
			msg = conn.recv(128).decode("UTF-8")
			if (HEX_MSG):
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				#print ("{} Test In > {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msg), msg ))
				print ("{} Test In > {}".format(timestamp, msg))
			readQueueSer.put(msg)
			conn.close()

	except OSError:
		print ("OSError: {}".format(inspect.stack()[0][3]))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None





# --------------------------------------------------------------------------------
#	Event Processing
# --------------------------------------------------------------------------------

"""
Process messages that arrive from DSC via the serial queue.
"""
def msghandler_dsc(readQueueSer, writeQueueSer, writeQueueNet, zones):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:

			msg = readQueueSer.get()
			if (msg.__len__() > 0):

				command = str(msg[0:3])
				data = str(msg[3:msg.__len__()])
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())

				# Track zone state changes
				if (command == NOTIFY_ZONE_OPEN):
					zoneobj = zones[int(data)-1]
					zoneobj.setState(ZONE_OPEN)
					zones[int(data)-1] = zoneobj
				elif (command == NOTIFY_ZONE_RESTORED):
					zoneobj = zones[int(data)-1]
					zoneobj.setState(ZONE_CLOSED)
					zones[int(data)-1] = zoneobj

				# All other messages relay to EVL
				writeQueueNet.put(dsc_send(msg))

	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None


"""
Process messages that arrive from the EVL client via the network queue
"""
def msghandler_evl(readQueueNet, writeQueueNet, writeQueueSer, zones):
	print("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:
			msg = readQueueNet.get()
			if (msg.__len__() > 0):

				# Print incoming message
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())

				# Decode message and handle it as appropriate
				command = str(msg[0:3])
				data = str(msg[3:(msg.__len__())])

				# --------------------------------------------------------------------------------
				# Message handlers
				# This needs to intercept EVL-specific messages and not send those to the panel
				# --------------------------------------------------------------------------------

				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				# Login
				if (command == EVL_LOGIN_REQUEST):
					writeQueueNet.put(dsc_send(EVL_LOGIN_INTERACTION + "1"))
				# Dump timers
				elif (command == EVL_DUMP_TIMERS):
					timermsg = ""
					for z in zones:
						timermsg += z.getTimer()
					writeQueueNet.put(dsc_send(EVL_DUMP_TIMER_RESPONSE + timermsg))
				# Key sequence
				# - Enables virtual keypad only while code is being sent
				elif (command == EVL_KEY_STRING):
					writeQueueSer.put(dsc_send(COMMAND_VIRTUAL_KEYBOARD_CONTROL + '1'))
					time.sleep(0.5)
					for c in data[1:]:
						keypress = dsc_send(COMMAND_KEY_PRESSED + c)
						writeQueueSer.put(keypress)
						time.sleep(0.25)
						keypress = dsc_send(COMMAND_KEY_PRESSED + '^')
						writeQueueSer.put(keypress)
						time.sleep(0.25)
					writeQueueSer.put(dsc_send(COMMAND_VIRTUAL_KEYBOARD_CONTROL + '0'))
				# Code padding (most commands require 6 digits, 4-digit codes need two zeros appended.
				# -- Partition disarm
				elif (command == COMMAND_PARTITION_DISARM_CONTROL):
					disarm_zone = data[0]
					disarm_code = data[1:]
					if (len(disarm_code)  == 4):
						disarm_code += '00'
					writeQueueSer.put(dsc_send(command + disarm_zone + disarm_code))
				# -- Code request
				elif (command == COMMAND_CODE_SEND):
					if (len(data)  == 4):
						data += '00'
					# DSC documentation is incorrect here.  Need to send partition number ahead of code.
					# Ideally pyenvisalink would do this correctly by remembering the partition from the '900'.
					writeQueueSer.put(dsc_send(command + '1' + data))
				# Customizations
				# - Change "arm stay" to "arm zero entry delay"
				elif (command == COMMAND_PARTITION_ARM_CONTROL_STAY):
					writeQueueSer.put(dsc_send(COMMAND_PARTITION_ARM_CONTROL_ZERO_ENTRY + data))

				# All other messages just relay to DSC as-is
				else:
					writeQueueSer.put(dsc_send(msg))

	except KeyboardInterrupt:
		pass
	except:
		print("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	print("Exiting {}".format(inspect.stack()[0][3]))
	return None



# --------------------------------------------------------------------------------
#	Helper functions
# --------------------------------------------------------------------------------

# Return checksum string for a given message
def dsc_checksum(msg):
	total = 0
	for i in msg:
		total += ord(i)
	total = total % 256

	return "{:02X}".format(total)

# Append checksum and CR/LF for outgoing messages
def dsc_send(msg):
	msg += dsc_checksum(msg)
	msg += chr(0x0D)
	msg += chr(0x0A)
	return msg


# Signal handler
def signal_handler(signal, frame):
	print("Signal handler called with signal {}".format(signal))
	sys.exit(0)



# --------------------------------------------------------------------------------
#	MAIN
# --------------------------------------------------------------------------------

if __name__ == "__main__":
	try:
		print("Process: {}".format(os.getpid()))

		# Open serial port
		ser = None
		ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=None)

		# Create socket
		sock = socket.socket()

		# Create shared queues for inter-process message handling
		readQueueSer = multiprocessing.Queue()
		writeQueueSer = multiprocessing.Queue()
		readQueueNet = multiprocessing.Queue()
		writeQueueNet = multiprocessing.Queue()
		
		# Create shared data space
		mgr = multiprocessing.Manager()
		zones = mgr.list()

		# Allocate zone objects
		for z in range(64):
			zones.append(dsc_zone(zone=z))


		# Start worker threads
		p_serialread = multiprocessing.Process(target=serialRead, args=(readQueueSer, ser))
		p_serialwrite = multiprocessing.Process(target=serialWrite, args=(writeQueueSer, ser))
		p_msghandler_dsc = multiprocessing.Process(target=msghandler_dsc, args=(readQueueSer, writeQueueSer, writeQueueNet, zones))
		p_msghandler_evl = multiprocessing.Process(target=msghandler_evl, args=(readQueueNet, writeQueueNet, writeQueueSer, zones))

		# Startup threads
		p_serialread.start()
		p_serialwrite.start()
		p_msghandler_dsc.start()
		p_msghandler_evl.start()

		# Stop execution here until a SIGINT or SIGTERM is received.  At this point all the work is being done by subprocesses and this function is just waiting to exit
		signal.signal(signal.SIGINT, signal_handler)
		signal.signal(signal.SIGTERM, signal_handler)

		time.sleep(3)

		print("---------- Panel Initialization Start ----------")
		writeQueueSer.put(dsc_send(COMMAND_POLL))
#		time.sleep(1)
#		writeQueueSer.put(dsc_send(COMMAND_VIRTUAL_KEYBOARD_CONTROL + '1'))
		time.sleep(2)
#		writeQueueSer.put(dsc_send(COMMAND_STATUS_REQUEST))
		print("---------- Panel Initialization End ----------")

		# Startup test network connection
		p_networkreadtest = multiprocessing.Process(target=networkReadTest, args=(readQueueSer, ))
		p_networkreadtest.start()

		# Handle network connections
		sock.bind((NETWORK_HOST, NETWORK_PORT))
		sock.setblocking(1)
		sock.listen(5)
		print ("EVL listening on {}:{}".format(NETWORK_HOST, str(NETWORK_PORT)))
		while(1):
			"""
				This should only attempt to handle one client at a time.
				If a new client connects, the queues are flushed and the network read/write threads are destroyed and recreated for the new connection.
				The client should immediately be sent the login interaction message to request authentication.
			"""
			# Wait for client connection.
			conn, addr = sock.accept()
			print ("Client connected: {}".format(addr))

			# Flush queues
			while ( readQueueSer.empty() == False ): readQueueSer.get()
			while ( readQueueNet.empty() == False ): readQueueNet.get()
			while ( writeQueueSer.empty() == False ): writeQueueSer.get()
			while ( writeQueueNet.empty() == False ): writeQueueNet.get()

			# Terminate old network I/O threads if they exist
			if 'p_networkread' in locals(): p_networkread.terminate()
			if 'p_networkwrite' in locals(): p_networkwrite.terminate()

			# Create new network I/O threads for this connection
			p_networkread = multiprocessing.Process(target=networkRead, args=(readQueueNet, conn))
			p_networkwrite = multiprocessing.Process(target=networkWrite, args=(writeQueueNet, conn))
			p_networkread.start()
			p_networkwrite.start()

			# Ask client to log in.  After this happens, the message handler thread will do the remainder of the interaction with the client
			writeQueueNet.put(dsc_send(EVL_LOGIN_INTERACTION + '3'))

		signal.pause()

	except (serial.serialutil.SerialException):
		print("Can't open port")
		
	except (KeyboardInterrupt, SystemExit):
		raise
	except:
		print("Caught exception in main: {}".format(sys.exc_info()[0]))
		raise
	finally:
		print("Terminating threads")
		if 'p_serialread' in locals(): p_serialread.terminate()
		if 'p_serialwrite' in locals(): p_serialwrite.terminate()
		if 'p_networkread' in locals(): p_networkread.terminate()
		if 'p_networkwrite' in locals(): p_networkwrite.terminate()
		if 'p_msghandler_dsc' in locals(): p_msghandler_dsc.terminate()
		if 'p_msghandler_evl' in locals(): p_msghandler_evl.terminate()
		if 'p_networkreadtest' in locals(): p_networkreadtest.terminate()

		ser.close()
		sock.shutdown(socket.SHUT_RDWR)
		sock.close()

		print("Done.")
