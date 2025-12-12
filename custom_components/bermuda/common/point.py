
import bisect
import math
import json


POINT_FRESH_CUT: float  = 1.5

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
			# _LOGGER.debug("device: %s", beacon.name)
			self.beacon_name = beacon.name
			self.beacon_address = beacon.address
			self.data = {}

			for advert in beacon.adverts.values():
				# rawdist = advert.set_ref_power(new_ref_power)
				# _LOGGER.debug("  advert.scanner_device.name: %s dist: %s dist_raw: %s", advert.scanner_device.name, advert.rssi_distance, advert.rssi_distance_raw)
				# _LOGGER.debug("name: %64s  stamp: %32s  dist: %32s  dist_raw: %32s", advert.scanner_device.name, advert.stamp, advert.rssi_distance, advert.rssi_distance_raw)
				self.data[advert.scanner_device.name] = {
					'stamp' : advert.stamp,
					'dist' : advert.rssi_distance,
					'dist_raw' : advert.rssi_distance_raw,
					'rssi' : advert.rssi,
				}

			_LOGGER.debug("Point  norm:  %s", beacon.name)
			self.log()

		@classmethod
		def get_fresh(self, beacon: BermudaDevice, time_cut: float = POINT_FRESH_CUT):# -> BermudaPoint: #seriously wtf?
			ret = BermudaPoint()
			ret.beacon_name = beacon.name
			ret.beacon_address = beacon.address

			ms = 0.0
			for advert in beacon.adverts.values():
				ms = max(stamp, advert.stamp)

			ret.data = {}
			for advert in beacon.adverts.values():
				if ms - advert.stamp < time_cut:
					ret.data[advert.scanner_device.name] = {
						'stamp' : advert.stamp,
						'dist' : advert.rssi_distance,
						'dist_raw' : advert.rssi_distance_raw,
						'rssi' : advert.rssi,
					}

			_LOGGER.debug("Point fresh:  %s", beacon.name)
			self.log()
			return ret

		def log(self):
			ms = self.get_max_stamp()
			for name in self.data.keys():
				_LOGGER.debug("\tname: %32s  stamp: %6.2f  rssi: %4s  dist: %6.2f  dist_raw: %6.2f", name, self.data[name]['stamp'] - ms, self.data[name]['rssi'], self.data[name]['dist'], self.data[name]['dist_raw'])

	def get_max_stamp(self):
		ms = 0.0
		for adv in self.data.values():
			ms = max(ms, adv['stamp'])
		return ms

	def fresh_cut(self, time_cut: float = POINT_FRESH_CUT):
		ms = self.get_max_stamp()

		ret = BermudaPoint(self.to_dict())
		for name in self.data.keys():
			if ms - self.data[name]['stamp'] >= time_cut:
				ret.data.pop(name)
		if not TOOLS:
			_LOGGER.debug("Point   cut:  %s", getattr(self, 'beacon_name', 'None'))
			ret.log()
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


