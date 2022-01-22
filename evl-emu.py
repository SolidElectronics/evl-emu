#!/usr/bin/env python3

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
import argparse
from pathlib import Path

REQUIREMENTS = ['pyserial']

DEFAULT_PARTITIONS = 1
DEFAULT_ZONES = 64

SERIAL_PORT = '/dev/it100'
SERIAL_BAUD = 9600
NETWORK_HOST = '0.0.0.0'
NETWORK_PORT = 4025

# Zone state definitions
ZONE_OPEN = 0
ZONE_CLOSED = 1

# Option to enable PC5401 specific commands - PC5401 is an older version of IT100 with less functionality, but the protocol is almost identical
# The major difference is that PC5401 dose not have Virtual Keypad support.
PC5401 = False
# Option to remap the Stay arem command to Zero Entry arm command
REMAP_STAY_TO_ZEROENTRY = True
#the length of the code to be used at COMMAND_CODE_SEND. The requiered length mey wary on panel configuration/type 
CODE_LENGTH = 4

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
COMMAND_DESCRIPTIVE_ARMING_CONTROL = '050'
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
NOTIFY_RING_DETECTED = '560'
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
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		lastdatatime = time.time()
		msgbuf = ''
		
		while True:
			read_byte = port.read(1)

			# If there is a long delay between messages while data is in the buffer, assume something went wrong and throw out the old data
			thisdatatime = time.time()
			if ( ((thisdatatime - lastdatatime) > 1.0) and (msgbuf.__len__() > 0) ):
				logger.info ("ERROR: flushing stale data from receive buffer {}".format(msgbuf))
				msgbuf=''

			# Add each byte of received data to message buffer
			# Use 'try' block to not explode on non-ASCII data.
			try:
				lastdatatime = time.time()
				msgbuf += read_byte.decode('ASCII')
			except:
				logger.error("Received invalid character in string \'{}\'".format(msgbuf[0:msgbuf.__len__()]))

			# If we have enough characters for a full message, start checking for CR/LR terminator
			if (msgbuf.__len__() >= 7):
				if (ord(msgbuf[msgbuf.__len__()-2]) == 0x0D and ord(msgbuf[msgbuf.__len__()-1]) == 0x0A):
					# Found terminator, message is complete.
					if (args.hex):
						logger.debug ("DSC In  > {}   {}".format(":".join("{:02X}".format(ord(c)) for c in msgbuf), msgbuf[0:msgbuf.__len__() - 2] ))
					else:
						logger.debug ("DSC In  > {}".format(msgbuf[0:msgbuf.__len__() - 4] ))

					# Queue message if checksum OK
					msgdata = msgbuf[0:msgbuf.__len__() - 4]
					msgchksum = msgbuf[msgbuf.__len__() - 4:msgbuf.__len__() - 2]
					if (msgchksum == dsc_checksum(msgdata)):
						readQueueSer.put(msgdata)
					else:
						timestamp=time.strftime("[%H:%M:%S]", time.localtime())
						logger.debug ("{} DSC In  > Checksum error".format(timestamp))
					msgbuf = ''

	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	logger.info("Exiting {}".format(inspect.stack()[0][3]))
	return None



"""
	Pull messages from write queue and send them to the serial port
	- Does not add checksum or CR/LF
"""
def serialWrite(writeQueueSer, port):
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:
			# Block until something is in the queue
			msg = writeQueueSer.get(True, None)

			# Send message
			if (args.hex):
				logger.debug ("DSC Out < {}   {}".format(":".join("{:02X}".format(ord(c)) for c in msg), msg[0:msg.__len__() - 2] ))
			else:
				logger.debug ("DSC Out < {}".format(msg[0:msg.__len__() - 4] ))

			port.write(bytes(msg, 'UTF-8'))

	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	logger.info("Exiting {}".format(inspect.stack()[0][3]))
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
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))
	
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
				logger.info ("ERROR: flushing stale data from receive buffer {}".format(msgbuf))
				msgbuf=''

			# Add each byte of received data to message buffer			
			msgbuf += read_byte.decode('ASCII')
			lastdatatime = time.time()

			# If we have enough characters for a full message, start checking for CR/LR terminator
			if (msgbuf.__len__() >= 7):
				if (ord(msgbuf[msgbuf.__len__()-2]) == 0x0D and ord(msgbuf[msgbuf.__len__()-1]) == 0x0A):
					# Found terminator, message is complete.
					if (args.hex):
						logger.debug ("EVL In  > {}   {}".format(":".join("{:02X}".format(ord(c)) for c in msgbuf), msgbuf[0:msgbuf.__len__() - 2] ))
					else:
						logger.debug("EVL In  > {}".format(msgbuf[0:msgbuf.__len__() - 4] ))
					# Queue message if checksum OK
					msgdata = msgbuf[0:msgbuf.__len__() - 4]
					msgchksum = msgbuf[msgbuf.__len__() - 4:msgbuf.__len__() - 2]
					if (msgchksum == dsc_checksum(msgdata)):
						readQueueNet.put(msgdata)
					else:
						timestamp=time.strftime("[%H:%M:%S]", time.localtime())
						logger.debug ("{} EVL In  > Checksum error".format(timestamp))
					msgbuf = ''

	except NameError:
		logger.error ("Connection closed by client, terminating networkRead thread. ({})".format(os.getpid()))
		conn.close()
		return None
	except OSError:
		logger.error ("OSError: {}".format(inspect.stack()[0][3]))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {} ({})".format(inspect.stack()[0][3], sys.exc_info()[0], os.getpid()))
		raise
	logger.info("Exiting {} ({})".format(inspect.stack()[0][3], os.getpid()))
	return None


"""
	Pull messages from write queue and send them to the socket connection
	- Does not add checksum or CR/LF
"""
def networkWrite(writeQueueNet, conn):
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		while True:
			# This should block until something is in the queue
			msg = writeQueueNet.get(True, None)

			# Print message and send it out the socket connection
			if (args.hex):
				logger.debug("EVL Out < {}   {}".format(":".join("{:02X}".format(ord(c)) for c in msg), msg[0:msg.__len__() - 2] ))
			else:
				logger.debug("EVL Out < {}".format(msg[0:msg.__len__() - 4] ))
			conn.send(bytes(msg, 'UTF-8'))

	except OSError:
		logger.error ("OSError: {} ({})".format(inspect.stack()[0][3], os.getpid()))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {} ({})".format(inspect.stack()[0][3], sys.exc_info()[0], os.getpid()))
		raise
	logger.info("Exiting {} ({})".format(inspect.stack()[0][3], os.getpid()))
	return None


"""
	Test function
	Open a network socket to accept fake commands that look like they're originating from the panel
	Note: Don't send checksum or CR/LF, they're not needed.
"""
def networkReadTest(readQueueSer):
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

	try:
		sock = socket.socket()
		sock.bind((NETWORK_HOST, (1 + NETWORK_PORT)))
		sock.setblocking(1)
		sock.listen(5)
	
		logger.info ("Test listening on {}:{}".format(NETWORK_HOST, str(1 + NETWORK_PORT)))
		while True:
			conn, addr = sock.accept()
			msg = conn.recv(128).decode("UTF-8")
			if (args.hex):
				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				#logger.info ("{} Test In > {}   {}".format(timestamp, ":".join("{:02X}".format(ord(c)) for c in msg), msg ))
				logger.debug ("{} Test In > {}".format(timestamp, msg))
			readQueueSer.put(msg)
			conn.close()

	except OSError:
		logger.error ("OSError: {}".format(inspect.stack()[0][3]))
		conn.close()
		return None
	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {}".format(inspect.stack()[0][3], sys.exc_info()[0]))
		raise
	logger.info("Exiting {}".format(inspect.stack()[0][3]))
	return None



# --------------------------------------------------------------------------------
#	Event Processing
# --------------------------------------------------------------------------------

"""
Process messages that arrive from DSC via the serial queue.
Only runs while client is connected.
"""
def msghandler_dsc(readQueueSer, writeQueueSer, writeQueueNet, zones, curent_part):
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

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

				# Readable logging for panel-originated notifications.  Some liberty taken with deciding what's info vs. debug
				# -- Panel
				if (command == NOTIFY_ACK):
					logger.info ("Panel: ack {}".format(data))
				elif (command == NOTIFY_VERSION):
					logger.info("Panel: version {}".format(data))
				elif (command == NOTIFY_CODE_REQUIRED):
					if (len(data) > 1):
						logger.info("Panel: code required. Partition {}, {} digits".format(int(data[0]), int(data[1])))
						curent_part.set(data[0])
					else:
						logger.info("Panel: code required.")
				elif (command == NOTIFY_RING_DETECTED):
					logger.info("Panel: ring detected")
				# -- Zones
				elif (command == NOTIFY_ZONE_OPEN):
					logger.debug("Panel: zone {} open".format(int(data)))
				elif (command == NOTIFY_ZONE_RESTORED):
					logger.debug("Panel: zone {} restored".format(int(data)))
				# -- Partitions
				elif (command == NOTIFY_PARTITION_BUSY):
					logger.info("Panel: partition {} busy".format(int(data)))
				elif (command == NOTIFY_PARTITION_TROUBLE):
					logger.info("Panel: partition {} trouble".format(int(data)))
				elif (command == NOTIFY_PARTITION_TROUBLE_RESTORED):
					logger.info("Panel: partition {} trouble restored".format(int(data)))
				elif (command == NOTIFY_PARTITION_ARMED):
					logger.info("Panel: partition {} armed (mode {})".format(int(data[0]), int(data[1])))
				elif (command == NOTIFY_PARTITION_IN_ALARM):
					logger.info("Panel: partition {} in alarm".format(int(data)))
				elif (command == NOTIFY_PARTITION_DISARMED):
					logger.info("Panel: partition {} disarmed".format(int(data)))
				elif (command == NOTIFY_PARTITION_EXIT_DELAY):
					logger.info("Panel: partition {} doing exit delay".format(int(data)))
				elif (command == NOTIFY_PARTITION_ENTRY_DELAY):
					logger.info("Panel: partition {} doing entry delay".format(int(data)))
				elif (command == NOTIFY_PARTITION_SPECIAL_CLOSING):
					logger.info("Panel: partition {} special closing (armed)".format(int(data)))
				# (debug messages)
				elif (command == NOTIFY_PARTITION_READY):
					logger.debug("Panel: partition {} ready".format(int(data)))
				elif (command == NOTIFY_PARTITION_NOT_READY):
					logger.debug("Panel: partition {} not ready".format(int(data)))
				elif (command == NOTIFY_PARTITION_USER_OPENING):
					logger.debug("Panel: partition {} opened (disarmed) by user {}".format(int(data[0]), int(data[1:])))
				elif (command == NOTIFY_PARTITION_USER_CLOSING):
					logger.debug("Panel: partition {} armed by user {}".format(int(data[0]), int(data[1:])))
				# -- Everything else
				else:
					logger.info("Panel: {}:{}".format(command, data))

	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {} ({})".format(inspect.stack()[0][3], sys.exc_info()[0], os.getpid()))
		raise
	logger.info("Exiting {} ({})".format(inspect.stack()[0][3], os.getpid()))
	return None


"""
Process messages that arrive from the EVL client via the network queue
Only runs while client is connected.
"""
def msghandler_evl(readQueueNet, writeQueueNet, writeQueueSer, zones, curent_part):
	logger.info("Starting {} ({})".format(inspect.stack()[0][3], os.getpid()))

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

				writeQueueNet.put(dsc_send(NOTIFY_ACK + command))

				timestamp=time.strftime("[%H:%M:%S]", time.localtime())
				# Login
				# - This shouldn't generally happen since login is handled before this starts up.
				if (command == EVL_LOGIN_REQUEST):
					logger.info("Client: EVL login request, replying with login success message")
					writeQueueNet.put(dsc_send(EVL_LOGIN_INTERACTION + "1"))
				# Dump timers
				elif (command == EVL_DUMP_TIMERS):
					logger.info("Client: EVL refresh zone timers")
					timermsg = ""
					for z in zones:
						timermsg += z.getTimer()
					writeQueueNet.put(dsc_send(EVL_DUMP_TIMER_RESPONSE + timermsg))
				# Key sequence
				# - Enables virtual keypad only while code is being sent
				elif (command == EVL_KEY_STRING):
					logger.info("Client: EVL virtual keypress {}".format(data[1:]))
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
					#logger.info("Client: disarm partition {} with code {}".format(data[0], data[1:]))
					logger.info("Client: disarm partition {} with code".format(data[0]))
					disarm_zone = data[0]
					disarm_code = data[1:]
					if (len(disarm_code)  == 4):
						disarm_code += '00'
					writeQueueSer.put(dsc_send(command + disarm_zone + disarm_code))
				# -- Code request
				elif (command == COMMAND_CODE_SEND):
					#logger.info("Client: sending code {}".format(data))
					logger.info("Client: sending code")
					if (CODE_LENGTH == 6) and (len(data)  == 4):
						data += '00'
					if curent_part.get() != '0':
						data = curent_part.get() + data
						curent_part.set('0')
					writeQueueSer.put(dsc_send(command + data))
				# Customizations
				# - Change "arm stay" to "arm zero entry delay"
				elif (command == COMMAND_PARTITION_ARM_CONTROL_STAY):
					if (REMAP_STAY_TO_ZEROENTRY == True):
						logger.info("Client: arm partition (zero entry)")
						writeQueueSer.put(dsc_send(COMMAND_PARTITION_ARM_CONTROL_ZERO_ENTRY + data))
					else:
						logger.info("Client: arm partition (Stay)")
						writeQueueSer.put(dsc_send(COMMAND_PARTITION_ARM_CONTROL_STAY + data))
				# All other messages just relay to DSC as-is
				else:
					writeQueueSer.put(dsc_send(msg))

					# Nice logging for client-originated commands
					if (command == COMMAND_POLL):
						logger.info ("Client: Poll/Keepalive")
					elif (command == COMMAND_STATUS_REQUEST):
						logger.info ("Client: Status request")
					elif (command == COMMAND_PARTITION_ARM_CONTROL_AWAY):
						logger.info ("Client: Arm partition (away)")
					elif (command == COMMAND_SET_TIME_DATE):
						logger.info ("Client: Set time on panel (hhmmMMDDYY): {}".format(data))
					else:
						logger.info("Client: {}:{}".format(command, data))

	except KeyboardInterrupt:
		pass
	except:
		logger.debug("Caught exception in {}: {} ({})".format(inspect.stack()[0][3], sys.exc_info()[0], os.getpid()))
		raise
	logger.info("Exiting {} ({})".format(inspect.stack()[0][3], os.getpid()))
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
	logger.info("Signal handler called with signal {}".format(signal))
	sys.exit(0)



# --------------------------------------------------------------------------------
#	MAIN
# --------------------------------------------------------------------------------

"""
	Startup serial I/O routines
	Wait for a network connection
	On connection:
		Handle client login
		Create network handler processes to read/write data from network queues
		Create event handler processes to do interactions
"""

if __name__ == "__main__":

	parser = argparse.ArgumentParser(description='Envisalink emulator for DSC-IT100')
	parser.add_argument('--debug', action='store_true', help="Enable debug messages")
	parser.add_argument('--info', action='store_true', help="Enable info messages")
	parser.add_argument('--hex', action='store_true', help="Add hex codes to debug messages")
	parser.add_argument('--console', action='store_true', help="enable logging to console")
	
	args = parser.parse_args()

	# Setup logging
	logger = logging.getLogger(__name__)
	#fileformatter = logging.Formatter(fmt='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
	fileformatter = logging.Formatter(fmt='%(asctime)s - %(message)s', datefmt='%m/%d %H:%M:%S')
	consoleformatter = logging.Formatter(fmt='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
	logger.setLevel(logging.WARNING)

	# File log options (Just put in home directory for now)
	logpath = str(os.path.expanduser('~')) + '/evl-emu.log'
	fh = logging.FileHandler(logpath)
	fh.setFormatter(fileformatter)
	logger.addHandler(fh)

	# Console log options
	ch = logging.StreamHandler()
	ch.setFormatter(consoleformatter)

	if (args.info):
		logger.setLevel(logging.INFO)
	if (args.debug):
		logger.setLevel(logging.DEBUG)

	if (args.console):
		logger.addHandler(ch)

	try:
		logger.info("-------------------- STARTUP --------------------")
		logger.warning("Startup Process: {}".format(os.getpid()))

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
		curent_part = mgr.Value(str,'0')

		# Allocate zone objects
		for z in range(DEFAULT_ZONES):
			zones.append(dsc_zone(zone=z))

		# Create and start serial I/O handlers
		p_serialread = multiprocessing.Process(target=serialRead, args=(readQueueSer, ser))
		p_serialwrite = multiprocessing.Process(target=serialWrite, args=(writeQueueSer, ser))
		p_serialread.start()
		p_serialwrite.start()

		# Create a signal object that will stop execution until a SIGINT or SIGTERM is received.  Don't start it yet.
		signal.signal(signal.SIGINT, signal_handler)
		signal.signal(signal.SIGTERM, signal_handler)

		# Startup test network connection
		#p_networkreadtest = multiprocessing.Process(target=networkReadTest, args=(readQueueSer, ))
		#p_networkreadtest.start()

		# Initialize panel
		# - Disable timestamps
		writeQueueSer.put(dsc_send(COMMAND_TIME_STAMP_CONTROL + '0'))
		# - Disable time/date broadcast messages
		writeQueueSer.put(dsc_send(COMMAND_TIME_DATE_BCAST_CONTROL + '0'))
		if (PC5401 == True):
			# - Enable descriptive arming control
			writeQueueSer.put(dsc_send(COMMAND_DESCRIPTIVE_ARMING_CONTROL + '1'))
		# Setup a network socket and listen for connections
		sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sock.bind((NETWORK_HOST, NETWORK_PORT))
		sock.setblocking(1)
		sock.listen(5)
		logger.info ("EVL waiting for connection on {}:{}".format(NETWORK_HOST, str(NETWORK_PORT)))
		while(1):
			"""
				This should only attempt to handle one client at a time.
				If a new client connects, the queues are flushed and the network read/write threads are destroyed and recreated for the new connection.
				The client should immediately be sent the login interaction message to request authentication.
			"""
			# Wait for client connection.
			conn, addr = sock.accept()
			logger.warning ("Client connected: {}".format(addr))

			# Flush queues
			logger.debug ("Flushing queues")
			while ( readQueueSer.empty() == False ): readQueueSer.get()
			while ( readQueueNet.empty() == False ): readQueueNet.get()
			while ( writeQueueSer.empty() == False ): writeQueueSer.get()
			while ( writeQueueNet.empty() == False ): writeQueueNet.get()

			# Terminate any running network or msghandler threads
			logger.debug ("Terminating old processes")
			if 'p_networkread' in locals() and p_networkread.is_alive(): p_networkread.terminate()
			if 'p_networkwrite' in locals() and p_networkwrite.is_alive(): p_networkwrite.terminate()
			if 'p_msghandler_dsc' in locals() and p_msghandler_dsc.is_alive(): p_msghandler_dsc.terminate()
			if 'p_msghandler_evl' in locals() and p_msghandler_evl.is_alive(): p_msghandler_evl.terminate()
			
			# This should actually wait and confirm the threads are shutdown before continuing.
			while(1):
				if ('p_networkread' not in locals() or not p_networkread.is_alive()): break
			while(1):
				if ('p_networkwrite' not in locals() or not p_networkwrite.is_alive()): break
			while(1):
				if ('p_msghandler_dsc' not in locals() or not p_msghandler_dsc.is_alive()): break
			while(1):
				if ('p_msghandler_evl' not in locals() or not p_msghandler_evl.is_alive()): break
			logger.debug ("All processes are stopped, continuing.")

			# Create and start network I/O handlers
			logger.debug ("Creating new p_networkread and p_networkwrite")
			p_networkread = multiprocessing.Process(target=networkRead, args=(readQueueNet, conn))
			p_networkwrite = multiprocessing.Process(target=networkWrite, args=(writeQueueNet, conn))
			p_networkread.start()
			p_networkwrite.start()

			# Do client login process.  Send password request (3), get any response, then send success message (1)
			logger.info ("Doing client login")
			writeQueueNet.put(dsc_send(EVL_LOGIN_INTERACTION + '3'))
			login = readQueueNet.get(True, 10)
			writeQueueNet.put(dsc_send(EVL_LOGIN_INTERACTION + "1"))

			# Create and start message handlers
			logger.debug ("Creating new p_msghandler_dsc and p_msghandler_evl")
			p_msghandler_dsc = multiprocessing.Process(target=msghandler_dsc, args=(readQueueSer, writeQueueSer, writeQueueNet, zones, curent_part))
			p_msghandler_evl = multiprocessing.Process(target=msghandler_evl, args=(readQueueNet, writeQueueNet, writeQueueSer, zones, curent_part))
			p_msghandler_dsc.start()
			p_msghandler_evl.start()

			logger.info("Client ready.")
			
			
		# Now stop execution until a signal is received.  At this point, all of the work is being done by the subprocesses.
		signal.pause()

	except (serial.serialutil.SerialException):
		logger.error("Can't open port")
		
	except (KeyboardInterrupt, SystemExit):
		raise
	except:
		logger.debug("Caught exception in main: {}".format(sys.exc_info()[0]))
		raise
	finally:
		logger.debug("Terminating threads")
		if 'p_serialread' in locals() and p_serialread.is_alive(): p_serialread.terminate()
		if 'p_serialwrite' in locals() and p_serialwrite.is_alive(): p_serialwrite.terminate()
		if 'p_networkread' in locals() and p_networkread.is_alive(): p_networkread.terminate()
		if 'p_networkwrite' in locals() and p_networkwrite.is_alive(): p_networkwrite.terminate()
		if 'p_msghandler_dsc' in locals() and p_msghandler_dsc.is_alive(): p_msghandler_dsc.terminate()
		if 'p_msghandler_evl' in locals() and p_msghandler_evl.is_alive(): p_msghandler_evl.terminate()
		if 'p_networkreadtest' in locals() and p_networkreadtest.is_alive(): p_networkreadtest.terminate()

		ser.close()
		sock.shutdown(socket.SHUT_RDWR)
		sock.close()

		logger.error("Exited.")
