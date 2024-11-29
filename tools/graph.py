#!/usr/bin/env python3

import json
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

# from matplotlib.widgets import Slider
from matplotlib.widgets import RadioButtons, CheckButtons
# import mpl_interactions.ipyplot as iplt

import sys, os
# sys.path.insert(1, '../custom_components/bermuda')	#ehhh
# sys.path.insert(1, '../')	#ehhh
# sys.path.insert(1, '../custom_components')	#ehhh
# sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/../")
# sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/../custom_components")
sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/../custom_components/bermuda")

# from ..custom_components.bermuda.map import *
# from ..custom_components.bermuda.point import *
from common.map import *
from common.point import *

path = 'data.json'
# print ('argument list', sys.argv)
if len(sys.argv) == 2:
    path = sys.argv[1]

with open(path, 'r') as file:
    data = json.load(file)

bmap = BermudaMap(data['data']['options']['bmap'])

# bmap.bake()

# print(bmap.baked)

scanners = list(bmap.scanners)
areas = list(bmap.areas)
area_col = plt.rcParams['axes.prop_cycle'].by_key()['color']
# print('area_col', area_col)
def get_area_col(area, alpha=0.2):
	return (mcolors.to_rgba(area_col[areas.index(area)], alpha))

# measures = { 'rssi', 'dist', 'dist_raw' }
measures = [ 'rssi', 'dist', 'dist_raw' ]

time_cut = 0.2

fmeasures = []

for m in measures:
	defa = -1.0
	if m == 'rssi':
		defa = -110

	fm = m + '_fresh'
	fmeasures.append(fm)

	data[m] = {}
	data[fm] = {}
	for area, points in bmap._area_points.items():
		if points:
			data[m][area] = []
			data[fm][area] = []
			for s in scanners:
				# data[m][area].append(np.array([d[s][m] for d in points]))
				dat = np.ndarray(0)
				fdat = []
				for p in points:
					val = p.get(s, m)
					if val is not None:
						dat = np.append(dat, val)
						# dat.append(p[s][m])
					else:
						dat = np.append(dat, defa)
						# dat.append(defa)

					val = p.fresh_cut().get(s, m)
					if val is not None:
						fdat.append(val)
					else:
						fdat.append(defa)

				data[m][area].append(dat)
				# data[m][area].append(np.array(dat))
				data[fm][area].append(np.array(fdat))

measures.extend(fmeasures)

fig, ax = plt.subplot_mosaic(
	[
		['y', 'main', 'mm'],
		['y', 'main', 'area_mesh'],
		['x', 'sec', 'area_pt'],
		['x', 'sec', 'sm'],
		# ['t', 't', 't'],
	],
	width_ratios=[1, 6, 1],
	# height_ratios=[8, 1],
	layout='constrained',
	sharex = True,
	sharey = True,
	# subplot_kw={"projection": "3d"},
)

al = 0.3
x = 0
y = 1
# mm = measures.index('dist_fresh')
# mm = measures.index('dist_raw_fresh')
mm = measures.index(bmap.metric + '_fresh')
sm = mm
# sm = measures.index('dist_raw_fresh')
st_areas = bmap.areas
mesh_area = areas[0]
mesh_area = 'value'

def test(mx, my):
	coord = [None] * len(bmap.scanners)
	if mx >= 0:
		coord[x] = mx
	elif mx > -1:
		coord[x] = 0
	if my >= 0:
		coord[y] = my
	elif my > -1:
		coord[y] = 0

	if mesh_area == 'detect':
		ret = bmap._map_point(tuple(coord))
		# ret = bmap._map_point_nn(tuple(coord))
		if ret is not None:
			area = ret['area']
			if area in st_areas:
				# return (255, 0, 0, 255)
				return get_area_col(area)
		return (0.0, 0.0, 0.0, 0.0)

	ret = bmap._point_probs(tuple(coord))

	area = mesh_area
	if mesh_area == 'value':
		area = max(ret, key=ret.get)

	value = ret[area]
	value = np.clip(value / 100.0, 0.0, 1.0)
	return get_area_col(area, value)

	# if ret is None:
	# 	return 0.0
	# elif ret['area'] == mesh_area:
	# 	return 1.0
	# else:
	# 	return -1.0

def update_mesh():
	global al, x, y, mm, sm

	# return
	axes = ax['main']

	# mx, my = np.mgrid[-3:3:complex(0, N), -2:2:complex(0, N)]
	extent = [-1, 30, -1, 30]
	res = np.linspace(-1, 30, 100)
	ires = len(res)

	# mx, my = np.meshgrid(res, res)
	# print('mx', mx)
	# print('my', my)
	# mt = np.vectorize(test)
	# mz = mt(mx, my)
	# mz = np.moveaxis(mz, 0, 2)
	# mz = mx
	mz = np.full((ires, ires, 4), 0.0)
	for iy in range(ires):
		for ix in range(ires):
			mz[iy, ix] = test(res[ix], res[iy])

	# mz.permute(1, 2, 0)
	# print('mz', mz)
	# for iy
	# axes.contourf(mx, my, mz)
	# axes.pcolormesh(mx, my, mz, shading='nearest')
	axes.imshow(mz, origin='lower', extent=extent, interpolation=None, aspect='auto')
	# plt.draw()


def update_ms():
	global al, x, y, mm, sm

	axes = ax['main']
	# plt.xlabel(scanners[x])
	# plt.ylabel(scanners[y])
	axes.clear()

	# for name, dat in data[measures[mm]].items():
	# 	if name in st_areas:
	# 		axes.scatter(dat[x], dat[y], alpha=al, label=name)
	for name in bmap.areas:
		dat = data[measures[mm]][name]
		a = 0
		if name in st_areas:
			a = al
		axes.scatter(dat[x], dat[y], alpha=a, label=name)
		# axes.plot(dat[x], dat[y], alpha=a, label=name)

	# axes.set_xlabel(scanners[x])
	# axes.set_ylabel(scanners[y])
	axes.legend()

	update_mesh()

	fig.show()
	plt.draw()


def update_ss():
	global al, x, y, mm, sm

	axes = ax['sec']
	axes.clear()
	# for name, dat in data[measures[sm]].items():
	# 	if name in st_areas:
	# 		axes.scatter(dat[x], dat[y], alpha=al, label=name)
	for name in bmap.areas:
		dat = data[measures[sm]][name]
		# print(name)

		fuu = np.stack((dat[x], dat[y]), axis=-1)
		fu = []
		for i in range(len(fuu)):
			if fuu[i][0] >= 0 or fuu[i][1] >= 0:
				fu.append(fuu[i])

		values, counts = np.unique(fu, axis = 0, return_inverse=False, return_counts=True)
		# print('\nvalues:\n')
		# print(values)
		# print('\n\ncounts:\n')
		# print(counts)
		for i in range(len(counts)):
			counts[i] *= plt.rcParams['lines.markersize'] ** 2
		a = 0
		if name in st_areas:
			a = al
		if len(values):
			axes.scatter(values[:,0], values[:,1], s=counts, alpha=a, label=name)
		# axes.scatter(dat[x], dat[y], alpha=a, label=name)
		# axes.plot(dat[x], dat[y], alpha=a, label=name)

	# axes.hist2d(data[measures[mm]]['kitchen'][x], data[measures[mm]]['kitchen'][y])

	axes.legend()


def update():
	update_ss()
	update_ms()

	# .canvas.draw_idle()
	# fig.draw()
	# plt.draw()
	# fig.show()

# fig.legend()

def change_x(label):
	global x, y, mm, sm
	x = scanners.index(label)
	update()

def change_y(label):
	global x, y, mm, sm
	y = scanners.index(label)
	update()

def change_mm(label):
	global x, y, mm, sm
	# mm = label
	mm = measures.index(label)
	update()

def change_sm(label):
	global x, y, mm, sm
	sm = measures.index(label)
	update()

def change_mesh(label):
	global x, y, mm, sm, mesh_area
	mesh_area = label
	update_ms()

radio_background = 'lightgoldenrodyellow'

ax['x'].set_facecolor(radio_background)
radiox = RadioButtons(ax['x'], scanners, active = x,
						# label_props={'color': 'cmy', 'fontsize': [12, 14, 16]},
						# radio_props={'s': [16, 32, 64]}
						)
radiox.on_clicked(change_x)

ax['y'].set_facecolor(radio_background)
radioy = RadioButtons(ax['y'], scanners, active = y,
						# label_props={'color': 'cmy', 'fontsize': [12, 14, 16]},
						# radio_props={'s': [16, 32, 64]}
						)
radioy.on_clicked(change_y)

ax['mm'].set_facecolor(radio_background)
radio_mm = RadioButtons(ax['mm'], measures, active = mm)
radio_mm.on_clicked(change_mm)

ax['sm'].set_facecolor(radio_background)
radio_sm = RadioButtons(ax['sm'], measures, active = sm)
radio_sm.on_clicked(change_sm)

ax['area_mesh'].set_facecolor(radio_background)
radio_mesh = RadioButtons(ax['area_mesh'], ['detect', 'value'] + areas, active = 1)
radio_mesh.on_clicked(change_mesh)

ax['area_pt'].set_facecolor(radio_background)
room_check = CheckButtons(ax['area_pt'], areas, tuple(True for _ in range(len(areas))))

def change_areas(label):
	global x, y, mm, sm, st_areas
	st_areas = set(room_check.get_checked_labels())
	update()
room_check.on_clicked(change_areas)


tunefig, tuneax = plt.subplots(len(bmap.tune.names))
# tunefig = fig.add_subfigure(gridspec[:, 0])
sliders = {}

idx = 0
for name in bmap.tune.names:
	a = tuneax[idx]
	# a = tunefig.add_subplot()
	sliders[name] = plt.Slider(a, name, bmap.tune.rmin[name], bmap.tune.rmax[name], valinit=getattr(bmap.tune, name))
	idx += 1

def tune_update(val):
	# global bmap
	for name in bmap.tune.names:
		setattr(bmap.tune, name, sliders[name].val)

	bmap.bake()

	bmap.tune.print()

	# bmap.calc_stats()
	bmap.calc_stats2()

	# bmap.bake_nn()
	# bmap.calc_stats_nn()

	update_ms()

for slider in sliders.values():
	slider.on_changed(tune_update)

update_ss()
tune_update(0)
# update()

plt.show()

