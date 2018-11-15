'''
Created on 17 oct. 2018

@author: Axel Huynh-Phuc, Thibaud Gasser
'''

import traci
from shapely.geometry.linestring import LineString


def stop_vehicle(veh_id):
    traci.vehicle.remove(veh_id, traci.constants.REMOVE_PARKING)


def lanes_in_area(area):
    for lane_id in traci.lane.getIDList():
        polygon_lane = LineString(traci.lane.getShape(lane_id))
        if area.rectangle.intersects(polygon_lane):
            yield lane_id


def lock_area(area):
    for lane_id in lanes_in_area(area):
        print(f'Setting max speed of {lane_id} to 30.')
        traci.lane.setMaxSpeed(lane_id, 30)

    for veh_id in traci.vehicle.getIDList():
        traci.vehicle.rerouteTraveltime(veh_id, True)
