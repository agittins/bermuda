
import json
import bisect
import math
import numpy as np
import statistics
import time

from .point import BermudaPoint

TOOLS = False
try:
	from ..bermuda_device import BermudaDevice
	from ..const import (
		_LOGGER,
		)
except ImportError:
	TOOLS = True

NN = True
try:
	import torch
except ImportError:
	NN = False

if NN:
	dtype = torch.float
	device = "cpu"
	# device = "cuda" if torch.cuda.is_available() else "cpu"
	torch.set_default_device(device)

	NN_COORD_NONE = 100
	NN_AREA_BAD = 0
	NN_AREA_GOOD = 1

#sooo annoying
def DBG(fmt : str, *args, **kwargs):
	s = fmt.format(*args, **kwargs)
	if TOOLS:
		print(s)
	else:
		_LOGGER.debug("%s", s)

class Tunables():
	def __init__(self) -> None:
		# pass
		# self.value = {}
		self.names = list()
		self.rmin = {}
		self.rmax = {}

	def setup(self, name: str, val: float, rmin: float, rmax: float):
		# self.value[name] = val
		setattr(self, 'name', val)
		self.names.append(name)
		self.rmin[name] = rmin
		self.rmax[name] = rmax

	def print(self):
		print('\n\nTUNABLES:')
		for n in self.names:
			print('self.tune.' + n + ' =', getattr(self, n))
		print('\nTUNABLES END\n')

class BermudaMap():
	def __init__(self, data: dict) -> None:

		# self.metric = 'dist'
		self.metric = 'dist_raw'

		self.areas = set()
		self.scanners = set()
		self.coords = ('',)
		self.skip_coord = None

		self.stat_types = ['pure', 'dirty']
		self.stat_values = ['good', 'bad', 'miss']

		self.tune = Tunables()
		self.tune.setup('neighboor_count',			14,		1.1,	20.0)
		self.tune.setup('coord_missmatch_dist',		3.56,	0.0,	20.0)
		self.tune.setup('order_value',				4.992,	0.0,	10.0)
		self.tune.setup('order_power',				1.337,	0.1,	20.0)
		self.tune.setup('count_value',				1.783,	0.1,	10.0)
		self.tune.setup('distance_value',			1.0,	0.0,	10.0)
		self.tune.setup('distance_range',			4.98,	0.001,	10.0)
		self.tune.setup('value_pass_factor',		2.688,	0.9,	8.0)

		#dist
		self.tune.neighboor_count = 20
		self.tune.coord_missmatch_dist = 3.82
		self.tune.coord_value = 10.0
		self.tune.order_value = 5.585
		self.tune.order_power = 3.746
		self.tune.count_value = 1.388
		self.tune.distance_value = 4.0    #dist with exp
		self.tune.distance_range = 6.175
		self.tune.value_pass_factor = 3

		#dist_raw
		self.tune.neighboor_count = 20.0
		self.tune.coord_missmatch_dist = 9.451926088318196
		self.tune.order_value = 5.585
		self.tune.order_power = 3.746
		self.tune.count_value = 1.388
		self.tune.distance_value = 4.0
		self.tune.distance_range = 1.611654243658002
		self.tune.value_pass_factor = 3.0457876605073597

		self.tune.neighboor_count = 20.0
		self.tune.coord_missmatch_dist = 9.451926088318196
		self.tune.order_value = 1.1474162757421533
		self.tune.order_power = 3.746
		self.tune.count_value = 1.388
		self.tune.distance_value = 4.0
		self.tune.distance_range = 2.4884801217148764
		self.tune.value_pass_factor = 4.89359729039503

		self.tune.neighboor_count = 20.0
		self.tune.coord_missmatch_dist = 9.451926088318196
		self.tune.order_value = 1.1474162757421533
		self.tune.order_power = 3.746
		self.tune.count_value = 1.388
		self.tune.distance_value = 4.0
		self.tune.distance_range = 3.1549473112242126
		self.tune.value_pass_factor = 4.89359729039503

		self.tune.neighboor_count = 20.0
		self.tune.coord_missmatch_dist = 9.451926088318196
		self.tune.order_value = 1.1474162757421533
		self.tune.order_power = 3.746
		self.tune.count_value = 1.388
		self.tune.distance_value = 4.0
		self.tune.distance_range = 1.9895264641403068
		self.tune.value_pass_factor = 2.2341684935797055

		# self.tune.coord_missmatch_dist = 20.0	#hmm
		# self.tune.distance_range = 0.535
		# self.tune.value_pass_factor = 1.944

		# self.tune.coord_missmatch_dist = 20.0	#for map vis
		# self.tune.distance_range = 0.535
		# self.tune.value_pass_factor = 1.05

		self.load(data)

	def load(self, ret: dict):
		self._area_points = {}
		for area, points in ret.items():
			self.areas.add(area)
			self._area_points[area] = []
			for p in points:
				bp = BermudaPoint(p)
				self.scanners.update(bp.get_scanners())
				self._area_points[area].append(bp)
		self.coords = tuple(self.scanners)
		self.nn_areas = tuple(self.areas)
		# self.bake()
		# self.calc_stats()

	def to_dict(self) -> dict:
		ret = {}
		for area, points in self._area_points.items():
			ret[area] = []
			for bp in points:
				ret[area].append(bp.to_dict())
		return ret

	def clear_all(self, area: str):
		self._area_points = {}

	def clear_area(self, area: str):
		self._area_points[area] = []


	if not TOOLS:
		def map_point(self, device: BermudaDevice, point: BermudaPoint):
			point = point.fresh_cut()
			coord = self.get_coord(point)
			_LOGGER.debug("  CO: %s", coord)
			dbg = False
			# dbg = True
			ret = self._map_point(coord, device.area_name, dbg = dbg)
			_LOGGER.debug(" RET: %s", ret)
			return ret
		def value_point(self, device: BermudaDevice, point: BermudaPoint):
			point = point.fresh_cut()
			coord = self.get_coord(point)
			return self._point_probs(coord)


	def _map_point(self, coord: tuple, prev_area: str | None = None, dbg = False):

		# return self._probs_detect(self._point_probs(coord), dbg = dbg)

		# bp = self.get_bp(coord)
		# if len(bp.area) > 0:
		# 	_LOGGER.debug("REF: %s", point.to_dict())
		# 	_LOGGER.debug(" CO: %s", coord)
		# 	# Counter(got)
		# 	times = bp.area
		# 	_LOGGER.debug(" times: %s", times)
		# 	times_sort = sorted(times.items(), key=lambda item: item[1], reverse=False)
		# 	_LOGGER.debug(" times_sort: %s", times_sort)

		if dbg:
			DBG("CO: {}", coord)

		got = []
		for bp in self.baked.values():
			if self.skip_coord is not None and bp.coord == self.skip_coord:
				continue
			d = self._dist(coord, bp.coord)
			if d is None:
				continue
			# _LOGGER.debug(" cmp %f for point %s", d, p.to_dict())
			if not got or d < got[-1][0]:
				# bisect.insort(got, ("key": 2), key=lambda x: x["key"])
				if len(got) >= int(self.tune.neighboor_count):
					got.pop(-1)
				bisect.insort(got, (d, bp))

		value = {}
		for i in range(len(got)):
			g = got[i]
			if dbg:
				DBG(" GOT: {} : {}", g[0], g[1].area)
			pts = sum(g[1].area.values())

			# goes from tune_order_value to 1.0 according to power (at 1.0 linearly), unless I screwed up;p
			if int(self.tune.neighboor_count) > 1:
				oi = pow(1.0 - (i / (int(self.tune.neighboor_count) - 1)), self.tune.order_power)
				ov = 1 + oi * (self.tune.order_value - 1)
				if dbg:
					DBG(" oi {}  ov: {}", oi, ov)
				del oi #python's crap
			else:
				ov = self.tune.order_value

			for area in g[1].area.keys():
				if area not in value:
					value[area] = 0

				c = g[1].area[area]

				# dv = 1 / (1 + g[0])
				# dv *= self.tune.distance_value
				dv = self.tune.distance_value * math.exp(-g[0]*g[0] / (self.tune.distance_range*self.tune.distance_range))
				value[area] += ov + (dv + (c / pts) * self.tune.count_value)

		if dbg:
			DBG(" value: {}", value)

		value_sort = sorted(value.items(), key=lambda item: item[1], reverse=True)

		if dbg:
			DBG("value_sort: {}", value_sort)

		if len(value_sort) == 1:
			return { 'dist': value_sort[0][1], 'area': value_sort[0][0] }
		if len(value) >= 2:
			if value_sort[0][1] / (value_sort[1][1] + 0.00001) >= self.tune.value_pass_factor:
				return { 'dist': value_sort[0][1], 'area': value_sort[0][0], 'maybe': value_sort[1][0] }
		return None

	def _point_probs(self, coord: tuple, dbg = False):
		ret = {}
		for area in self.areas:
			ret[area] = 0
		for bp in self.baked.values():
			if self.skip_coord is not None and bp.coord == self.skip_coord:
				continue
			d = self._dist(coord, bp.coord)
			if d is None:
				continue
			dv = self._dist2value(d)
			# _LOGGER.debug(" cmp %f for point %s", d, p.to_dict())
			for area in bp.area.keys():
				tmp = dv * bp.area[area]
				ret[area] += tmp

		for area in ret.keys():
			pass
			# ret[area] *= self.area_mean[area]
			# ret[area] /= self.area_mean[area]
			# ret[area] *= self.area_mean_mean / self.area_mean[area]
			# ret[area] /= self.area_mean_mean / self.area_mean[area]
			# ret[area] /= self.area_mean[area]**0.1

		# ret = sorted(ret.items(), key=lambda item: item[1], reverse=True)
		return ret

	def _probs_detect(self, probs: dict, dbg = False):
		ret = {}

		psort = sorted(probs.items(), key=lambda item: item[1], reverse=True)

		if len(psort) == 1:
			return { 'dist': psort[0][1], 'area': psort[0][0] }
		if len(psort) >= 2:
			if psort[0][1] / (psort[1][1] + 0.00001) >= self.tune.value_pass_factor:
				return { 'dist': psort[0][1], 'area': psort[0][0], 'maybe': psort[1][0] }
		return None


	def bulid_coord(self, p: dict) -> tuple:
		coords = []#there probably is something more efficient?
		for scanner in self.coords:
			if scanner in p.data:
				coords.append(p.data[scanner][self.metric])
			else:
				coords.append(None)
		return tuple(coords)

	def get_coord(self, p: BermudaPoint) -> tuple:
		p = p.fresh_cut()
		coords = []#there probably is something more efficient?
		for scanner in self.coords:
			if scanner in p.data:
				coords.append(p.data[scanner][self.metric])
			else:
				coords.append(None)
		return tuple(coords)

	def _dist(self, c0, c1) -> float:
		dist = 0.0
		count = 0
		#TODO value short distances more since they're more reliable?
		#TODO instead of coord missmatch distance, it'd be better to just value coord matches?

		for i in range(len(self.coords)):
			if c0[i] is not None and c1[i] is not None:
				d = c0[i] - c1[i]
				dist += d * d
				count += 1
			elif c0[i] is not None or c1[i] is not None:
				d = self.tune.coord_missmatch_dist
				dist += d * d
		if count:
			return math.sqrt(dist)
		return None

	def _dist2value(self, d: float) -> float:
		# dv = 1 / (1 + d / self.tune.distance_range)
		# dv = 1 / (1 + d**2 / self.tune.distance_range)
		dv = math.exp(-d / self.tune.distance_range)
		# dv /= 0.2 * self.tune.distance_range**2	#normalize a bit so the graph image behaves better
		# dv = math.exp(-d**2 / (self.tune.distance_range**2))
		dv *= self.tune.distance_value
		return dv

	def get_bp(self, coord):
		if coord in self.baked:
			return self.baked[coord]
		bp = BermudaBakedPoint(coord)
		self.baked[coord] = bp
		return bp

	# def update(existing_aggregate, new_value):
	# 	(count, mean, M2) = existing_aggregate
	# 	count += 1
	# 	delta = new_value - mean
	# 	mean += delta / count
	# 	delta2 = new_value - mean
	# 	M2 += delta * delta2
	# 	return (count, mean, M2)

	# def finalize(existing_aggregate):
	# 	(count, mean, M2) = existing_aggregate
	# 	if count < 2:
	# 		return float("nan")
	# 	else:
	# 		(mean, variance, sample_variance) = (mean, M2 / count, M2 / (count - 1))
	# 		return (mean, variance, sample_variance)

	def bake(self):
		self.baked = {}
		# self.baked = []
		for area, points in self._area_points.items():
			for p in points:
				p = p.fresh_cut()
				coord = self.get_coord(p)

				bp = self.get_bp(coord)

				if area not in bp.area:
					bp.area[area] = 0
				bp.area[area] += 1

		#in the end we need to bake into somekindof actual map/grid (something like what the graph update_mesh() looks like)
		#but given the expected large dimensionality and sparcity it's probably best to have a few maps for whatever combinations of coordinates happen in practice?
		# on the plus side rssi/dist_raw is horribly discrete so it's very little data no matter what;p

		self.bake_mean()

	def bake_mean(self):
		self.area_count = {}
		self.area_mean = {}
		fu = list(self.baked.values())
		n = len(fu)
		for area in self.areas:
			mean = 0
			count = 0
			for i in range(n):
				bp = fu[i]
				if area not in bp.area:
					continue

				for j in range(i+1, n):
				# for j in range(n):
				# 	if i == j:
				# 		continue
					bp1 = fu[j]
					if area not in bp1.area:
						continue
					d = self._dist(bp.coord, bp1.coord)
					if d is not None:
						dv = self._dist2value(d)
						# mean += dv * 1
						# count += 1
						c = bp.area[area] + bp1.area[area]
						mean += dv * c
						count += c

			self.area_count[area] = count
			self.area_mean[area] = mean / count if count else 0
			DBG('MEAN {} COUNT {} for {}', self.area_mean[area], count, area)

		self.area_count_mean = statistics.mean(self.area_count.values())
		self.area_mean_mean = statistics.mean(self.area_mean.values())
		DBG('MEAN MEAN {} COUNT {}', self.area_mean_mean, self.area_count_mean)
		# for area in self.areas:
			# self.area_mean[area] = self.area_mean_mean / self.area_mean[area]
			# DBG('MEAN {} for {}', self.area_mean[area], area)

	def debug_point(self, bp):
		DBG('FUUUU START:')
		# ret = self._map_point(bp.coord, dbg = True)
		DBG(" CO: {}", bp.coord)
		ret = self._point_probs(bp.coord, dbg = True)
		DBG(" RET: {}	for: {}", ret, bp.sorted_areas())
		DBG('FUUUU END')

	def calc_stats(self):
		self.stats = {}
		for area in self.areas:
			self.stats[area] = {}
			self.stats[area]['count pure distinct'] = 0
			self.stats[area]['count pure total'] = 0
			self.stats[area]['count dirty distinct'] = 0
			self.stats[area]['count dirty total'] = 0

			for typ in self.stat_types:
				for sv in self.stat_values:
					self.stats[area][typ+' '+sv] = 0

		for bp in self.baked.values():
			self.skip_coord = bp.coord

			sa = bp.sorted_areas()
			if len(sa) == 1:
				typ = 'pure'
				self.stats[area]['count pure distinct'] += 1
				self.stats[area]['count pure total'] += sa[0][1]
			else:
				typ = 'dirty'
				for area, count in sa:
					self.stats[area]['count dirty distinct'] += 1
					self.stats[area]['count dirty total'] += count

			area = sa[0][0]

			ret = self._map_point(bp.coord)

			if ret is None:
				self.stats[area][typ + ' miss'] += sa[0][1]
			else:
				if area == ret['area']:
					self.stats[area][typ + ' good'] += sa[0][1]
				else:
					self.stats[area][typ + ' bad'] += sa[0][1]
					# if area == 'corridor':
						# self.debug_point(bp)
			# for area in bp.sorted_areas():
			# 	if area == ret['area']:
			# 		self.stats[area]
		# print(self.stats)
		DBG(self.stats_str())
		self.skip_coord = None
		return

	def calc_stats2(self):
		self.stats = {}
		for area in self.areas:
			self.stats[area] = {}
			self.stats[area]['count pure distinct'] = 0
			self.stats[area]['count pure total'] = 0
			self.stats[area]['count dirty distinct'] = 0
			self.stats[area]['count dirty total'] = 0
			self.stats[area]['mse'] = 0
			self.stats[area]['dirty mse'] = 0

			for typ in self.stat_types:
				for sv in self.stat_values:
					self.stats[area][typ+' '+sv] = 0

		for bp in self.baked.values():
			full = self._point_probs(bp.coord)
			self.skip_coord = bp.coord
			skip = self._point_probs(bp.coord)			#TODO super stupidly redundant

			ret = self._probs_detect(skip)

			sa = bp.sorted_areas()
			area = sa[0][0]

			# if area == 'kitchen':
				# self.debug_point(bp)
			if len(sa) == 1:
				typ = 'pure'
				self.stats[area]['count pure distinct'] += 1
				self.stats[area]['count pure total'] += sa[0][1]
				if ret is None:
					self.stats[area][typ + ' miss'] += sa[0][1]
				else:
					if area == ret['area']:
						self.stats[area][typ + ' good'] += sa[0][1]
					else:
						self.stats[area][typ + ' bad'] += sa[0][1]
			else:
				# print(skip)
				typ = 'dirty'
				# totre = sum(c for _, c in skip)
				totre = sum(skip.values())
				totbp = sum(c for _, c in sa)
				se = 0
				re = dict(skip)
				for area, count in sa:
					self.stats[area]['count dirty distinct'] += 1
					self.stats[area]['count dirty total'] += count
					if ret is None:
						self.stats[area][typ + ' miss'] += count
					else:
						if area == ret['area']:
							self.stats[area][typ + ' good'] += count
						else:
							self.stats[area][typ + ' bad'] += count
				self.stats[area]['dirty mse'] += math.sqrt(se)

			# totfull = sum(c for _, c in full)
			# totskip = sum(c for _, c in skip)
			# se = 0
			# re = dict(skip)
			# for area, count in full:
			# 	e = count / totfull - re[area] / totskip
			# 	se += e*e
			# 	self.stats[area]['mse'] += math.sqrt(se)

			# if area == 'basement':
			# 	self.debug_point(bp)
			# DBG('full {}', full)
			# DBG('skip {}', skip)
		# print(self.stats)
		DBG(self.stats_str())
		self.skip_coord = None
		return


	def stats_str(self) -> str:
		ret = ''
		# ret = 'tune_neighboor_count: {}\n'.format(self.tune.neighboor_count)
		gs = {}
		for v in self.stat_values:
			for typ in self.stat_types:
				gs[typ+' '+v] = 0

		gs['mse'] = 0
		for area, st in self.stats.items():
			ret += area.ljust(32)
			# ret += '\tpure {:>4} / {:<4}'.format(st['count pure distinct'], st['count pure total'])
			# ret += '\tdirty {:>4} / {:<4}'.format(st['count dirty distinct'], st['count dirty total'])
			# ret += '\t' + str(round(self.stats[area]['mse'], 4)).rjust(5) + ' mse'
			for typ in self.stat_types:
				ret += '\t{:>5} {:>4} / {:<4}'.format(typ, st['count '+typ+' distinct'], st['count '+typ+' total'])
			ret += '\n'
			# tot = sum(st.values())
			# gs['mse'] += st['mse']
			for v in self.stat_values:
				ret += v.rjust(12)
				for typ in self.stat_types:
					tot = self.stats[area]['count '+typ+' total']
					sv = typ+' '+v

					if tot:
						fl = st[sv] / tot
					else:
						fl = 0
					ret += '  ' + str(round(100 * fl, 1)).rjust(4) + ' %'
					gs[sv] += fl
				# if 'dirty mse' in self.stats[area]:
				# 	ret += '  ' + str(round(self.stats[area]['dirty mse'], 4)).rjust(5) + ' mse'
				ret += '\n'

		tot = len(self.stats)
		ret += 'global avg:'
		# ret += '\t\t\t\t' + str(round(self.stats[area]['mse'], 4)).rjust(5) + ' mse'
		ret += '\n'
		for v in self.stat_values:
			ret += v.rjust(10)
			for typ in self.stat_types:
				sv = typ+' '+v
				ret += '  ' + str(round(100 * gs[sv] / tot, 2)).rjust(5) + ' %'
			# if 'dirty mse' in gs:
			# 	ret += '  ' + str(round(gs['dirty mse'], 4)).rjust(5) + ' mse'
			ret += '\n'
		return ret


	if NN:
		def _coord2tens(self, coord):
			return torch.Tensor([NN_COORD_NONE if c is None else c for c in coord])

		def bake_nn(self):
			# x = torch.tensor([[0.1, 1.2], [2.2, 3.1], [4.9, 5.2]])
			# x = torch.tensor([])
			# y = torch.tensor([])
			# x = torch.empty((len(self.baked), len(self.coords)))
			x = torch.full((len(self.baked), len(self.coords)), NN_COORD_NONE, dtype=dtype)
			y = torch.full((len(self.baked), len(self.nn_areas)), NN_AREA_BAD, dtype=dtype)

			i = 0
			for bp in self.baked.values():
				sa = bp.sorted_areas()

				x[i] = self._coord2tens(bp.coord)
				tmp = [NN_AREA_BAD] * len(self.nn_areas)

				if len(sa) == 1:
					area = sa[0][0]

					tmp[self.nn_areas.index(area)] = NN_AREA_GOOD
					# y.append(tmp)
				else:
					tot = sum(c for _, c in sa)
					for area, count in sa:
						tmp[self.nn_areas.index(area)] = NN_AREA_BAD + (NN_AREA_GOOD - NN_AREA_BAD) / tot
				y[i] = torch.Tensor(tmp)
				i += 1

			print('X=', x)
			print('Y=', y)
			self.model = torch.nn.Sequential(
				# torch.nn.Linear(len(self.coords), len(self.areas)),

				torch.nn.Linear(len(self.coords), len(self.coords) * 2),
				# torch.nn.Tanh(),
				# torch.nn.ReLU(),
				# torch.nn.CELU(),
				# torch.nn.Sigmoid(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.ReLU(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.ReLU(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.ReLU(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Sigmoid(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Sigmoid(),
				torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Tanh(),
				# torch.nn.Linear(len(self.coords) * 100, len(self.coords) * 100),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),	torch.nn.Sigmoid(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.ReLU(),
				# # torch.nn.Sigmoid(),
				# torch.nn.Linear(len(self.coords) * 2, len(self.coords) * 2),
				# torch.nn.Sigmoid(),
				torch.nn.Linear(len(self.coords)*2, len(self.areas)),

				# torch.nn.Linear(len(self.coords), len(self.coords) * 100),
				# torch.nn.Linear(len(self.coords) * 100, len(self.coords) * 100),	torch.nn.Tanh(),
				# torch.nn.Linear(len(self.coords)*100, len(self.areas)),

				torch.nn.Sigmoid(),
				# torch.nn.ReLU(),
			)

			loss_fn = torch.nn.MSELoss(reduction='sum')
			optimizer = torch.optim.RMSprop(self.model.parameters(), lr=1e-3)

			t_end = time.time() + 15
			prev_loss = 0
			meh_count = 0
			i = 0
			while time.time() < t_end:
				y_pred = self.model(x)

				loss = loss_fn(y_pred, y)
				if i % 100 == 0:
					print("AAAAAA", i, loss.item())

				if abs(loss.item() - prev_loss) / loss.item() >= 1.0 / 1000.0:
					meh_count = 0
				else:
					meh_count += 1
					if i >= 1000 and meh_count > 10:
						print("loss.item()", loss.item())
						print("prev_loss()", prev_loss)
						print("abs(loss.item() - prev_loss)", abs(loss.item() - prev_loss))
						print("no progress", i, loss.item())
						break


				prev_loss = loss.item()
				# Before the backward pass, use the optimizer object to zero all of the
				# gradients for the variables it will update (which are the learnable
				# weights of the model). This is because by default, gradients are
				# accumulated in buffers( i.e, not overwritten) whenever .backward()
				# is called. Checkout docs of torch.autograd.backward for more details.
				optimizer.zero_grad()

				# Backward pass: compute gradient of the loss with respect to model
				# parameters
				loss.backward()

				# Calling the step function on an Optimizer makes an update to its
				# parameters
				optimizer.step()
				i += 1
			print(loss.item())

		def _map_point_nn(self, coord):
			x = self._coord2tens(coord)
			y = self.model(x)

			if NN_AREA_GOOD > NN_AREA_BAD:
				ind = torch.argmax(y).item()
			else:
				ind = torch.argmin(y).item()
			# print('ind=', ind)
			# print('y[ind]=', y[ind].item())
			# for i, v in enumerate(y):

			ret = {
				# 'dist': torch.index_select(y, 0, ind).item(),
				'dist': y[ind].item(),
				'area': self.nn_areas[ind]
			}

			for i, o in enumerate(y):
				# print(abs(y[ind].item()) / (abs(o.item()) + 0.00001))
				if i != ind and abs(y[ind].item()) / (abs(o.item()) + 0.00001) < self.tune.value_pass_factor:
					ret = None

			return ret

		def calc_stats_nn(self):
			self.stats = {}
			for area in self.areas:
				self.stats[area] = {}
				self.stats[area]['count pure distinct'] = 0
				self.stats[area]['count pure total'] = 0
				self.stats[area]['count dirty distinct'] = 0
				self.stats[area]['count dirty total'] = 0

				for typ in self.stat_types:
					for sv in self.stat_values:
						self.stats[area][typ+' '+sv] = 0

			for bp in self.baked.values():
				sa = bp.sorted_areas()
				if len(sa) == 1:
					typ = 'pure'
					self.stats[area]['count pure distinct'] += 1
					self.stats[area]['count pure total'] += sa[0][1]
				else:
					typ = 'dirty'
					for area, count in sa:
						self.stats[area]['count dirty distinct'] += 1
						self.stats[area]['count dirty total'] += count

				area = sa[0][0]

				ret = self._map_point_nn(bp.coord)

				if ret is None:
					self.stats[area][typ + ' miss'] += sa[0][1]
				else:
					if area == ret['area']:
						self.stats[area][typ + ' good'] += sa[0][1]
					else:
						self.stats[area][typ + ' bad'] += sa[0][1]
						# if area == 'corridor':
							# self.debug_point(bp)
				# for area in bp.sorted_areas():
				# 	if area == ret['area']:
				# 		self.stats[area]
			# print(self.stats)
			DBG(self.stats_str())
			self.skip_coord = None
			return


class BermudaBakedPoint():
	def __init__(self, coord) -> None:
		self.coord = coord
		self.area = {}

	def __lt__(self, other):
		for i in range(len(self.coord)):
			if self.coord[i] is not None:
				if other.coord[i] is None:
					return True
				elif self.coord[i] < other.coord[i]:
					return True
			elif other.coord[i] is not None:
				return False
		return False

	def sorted_areas(self):
		return sorted(self.area.items(), key=lambda item: item[1], reverse=True)



class BermudaMapTrack():
	def __init__(self, bmap: BermudaMap) -> None:
		self.bmap = bmap
		self.time_raw = 0.0
		self.time = 0.0

		self.value_raw = {}
		self.value_raw_hist = {}
		self.value_min = {}
		self.value_max = {}
		self.value = {}
		self.alpha = 0.8
		self.value_s0 = {}
		self.alpha_s0 = 0.5


		self.area = None

	if not TOOLS and True:
		def map_point(self, device: BermudaDevice, point: BermudaPoint):
			point = point.fresh_cut()

			self.time_raw = time.time()
			self.value_raw = self.bmap.value_point(device, point)

			# ret = device.maptrack.value_point(device, point)
			# for area, value in ret.items():
			    # device.value_raw[area] = self.value_raw

			# self.value_raw_hist.pop(0)
			# self.value_raw_hist.append(self.value_raw)

			sv = 0.00001

			DBG('self.time_raw = {}', self.time_raw)

			#TODO make it more 'pythonic'... ehh...

			for a, vr in self.value_raw.items():
				if a not in self.value_s0:
					self.value_s0[a] = 0.0
				self.value_s0[a] *= 1.0 - self.alpha_s0
				self.value_s0[a] += vr * self.alpha_s0

				if a not in self.value_raw_hist:
					self.value_raw_hist[a] = []
				if len(self.value_raw_hist[a]) >= 3:
					self.value_raw_hist[a].pop(0)
				self.value_raw_hist[a].append(vr)

				self.value_min[a] = min(self.value_raw_hist[a])
				self.value_max[a] = max(self.value_raw_hist[a])

				if a == self.area:
					self.value[a] = self.value_max[a]
				else:
					self.value[a] = self.value_min[a]

			# for a, vr in self.value_raw.items():#make it more 'pythonic'...
			# 	if a not in self.value:
			# 		self.value[a] = 0.0
			# 	vrpp = self.value_rawpp.get(a, 0.0)
			# 	vrp = self.value_rawp.get(a, 0.0)

			# 	self.value_s0[a] *= 1.0 - self.alpha_s0
			# 	self.value_s0[a] += vr * self.alpha_s0

			# 	v = self.value.get(a, 0.0)

			# 	if abs(self.value_s0[a] - v) / (v+sv)  >= 0.5:
			# 		a = self.alpha
			# 		self.value[a] *= 1.0 - self.alpha
			# 		self.value[a] += vr * self.alpha
			# 		continue

			# 	self.value[a] *= 1.0 - self.alpha
			# 	self.value[a] += v * self.alpha


			# coord = self.bmap.get_coord(point)

			psort = sorted(self.value.items(), key=lambda item: item[1], reverse=True)

			if len(psort) == 1:
				self.area = psort[0][0]
				device.apply_area(psort[0][1], self.area)
			if len(psort) >= 2:
				if (psort[0][1] + sv) / (psort[1][1] + sv) >= self.bmap.tune.value_pass_factor:
					self.area = psort[0][0]
					device.apply_area(psort[0][1], self.area, psort[1][0])
					# return { 'dist': psort[0][1], 'area': psort[0][0], 'maybe': psort[1][0] }
			# return
			# # _LOGGER.debug("  CO: %s", coord)
			# dbg = False
			# dbg = True
			# ret = self.bmap._map_point(coord, device.area_name, dbg = dbg)
			# _LOGGER.debug(" RET: %s", ret)
			# return ret

	if not TOOLS and False:
		def map_point(self, device: BermudaDevice, point: BermudaPoint):
			# _LOGGER.debug("REF  full: %s", point.to_dict())
			point = point.fresh_cut()
			# _LOGGER.debug("REF fresh: %s", point.to_dict())

			self.value_raw = self.bmap.value_point(device, point)

			# ret = self._probs_detect(self.value_raw)
			ret = self.bmap.map_point(device, point)

			# _LOGGER.debug("GOT: %s", ret)
			if isinstance(ret, dict) and 'dist' in ret and 'area' in ret:
			    device.apply_area(**ret)




