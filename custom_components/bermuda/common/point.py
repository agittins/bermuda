
import bisect
import math
import json

TOOLS = False
try:
	from ..bermuda_device import BermudaDevice
	from ..const import (
		_LOGGER,
	)
	from ..util import rssi_to_metres
except ImportError:
	TOOLS = True

class BermudaPoint():
	def __init__(self,
		smth#: BermudaDevice, # The device/beacon for which we capture this point
	) -> None:
		if not TOOLS:
			if isinstance(smth, BermudaDevice):
				self._init(smth)
				return

		if isinstance(smth, dict):
			self.from_dict(smth)
		else:
			self.data = {}

	if not TOOLS:
		def _init(self, beacon: BermudaDevice) -> None:
			_LOGGER.debug("device: %s", beacon.name)
			self.beacon_address = beacon.address
			self.data = {}
			for scanner in beacon.scanners.values():
				_LOGGER.debug("  scanner.scanner_device.name: %s", scanner.scanner_device.name)
				# self.data[scanner.scanner_device.name] = scanner.rssi_distance
				self.data[scanner.scanner_device.name] = {
					'stamp' : scanner.stamp,
					'dist' : scanner.rssi_distance,
					'dist_raw' : scanner.rssi_distance_raw,
					'rssi' : scanner.rssi,
				}

		@classmethod
		def get_fresh(self, beacon: BermudaDevice):# -> BermudaPoint: #seriously wtf?
			ret = BermudaPoint()
			ret.beacon_address = beacon.address

			stamp = 0.0
			for scanner in beacon.scanners.values():
				stamp = max(stamp, scanner.stamp)

			ret.data = {}
			for scanner in beacon.scanners.values():
				if stamp - scanner.stamp <= 0.5:
					ret.data[scanner.scanner_device.name] = {
						'stamp' : scanner.stamp,
						'dist' : scanner.rssi_distance,
						'dist_raw' : scanner.rssi_distance_raw,
						'rssi' : scanner.rssi,
						}
			return ret

	def fresh_cut(self, time_cut: float = 0.2):
		ms = 0.0
		for name in self.data.keys():
			ms = max(ms, self.data[name]['stamp'])

		ret = BermudaPoint(self.to_dict())
		for name in self.data.keys():
			if ms - self.data[name]['stamp'] >= time_cut:
				ret.data.pop(name)

		return ret

	def from_dict(self, d) -> None:
		self.data = d.copy()
	def to_dict(self) -> dict:
		return self.data.copy()

	def get_scanners(self) -> set:
		return set(self.data.keys())

	def get(self, scanner: str, measure: str) -> float | None:
		if scanner in self.data:
			if measure in self.data[scanner]:
				return self.data[scanner][measure]
		return None

	def dist(self, p#: BermudaPoint # welp, this has felt like pulling teeth, now I'm certain of it...
		):
		dist = 0.0
		count = 0
		# if not TOOLS:
			# _LOGGER.debug(" self.data %s", self.data)
			# _LOGGER.debug("    p.data %s", p.data)
		m = 'dist'
		for name in self.data.keys():
			if self.data[name] is None or m not in self.data[name] or self.data[name][m] is None:
				continue
			if name in p.data:
				if p.data[name] is None or m not in p.data[name] or p.data[name][m] is None:
					continue
				# dist += abs(p1[name] - self.data[name])
				# d = p.data[name] - self.data[name]
				d = p.data[name][m] - self.data[name][m]
				dist += d * d
				count += 1
		if count:
			dist = math.sqrt(dist)
			# return dist / count
			return dist
		return None

		# out = {}
		# for var, val in vars(self).items():
		# 	if var == "scanners":
		# 		scanout = {}
		# 		for address, scanner in self.scanners.items():
		# 			scanout[address] = scanner.to_dict()
		# 			# FIXME: val is overwritten
		# 			val = scanout  # noqa
		# 			out[var] = val
		# 			return out


