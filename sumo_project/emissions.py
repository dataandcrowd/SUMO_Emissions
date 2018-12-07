from typing import List

import traci
import logging
import time

from shapely.geometry import LineString
from parse import *

import actions
import config
import sys
from model import Area, Vehicle, Lane , TrafficLight , Phase , Logic
from traci import trafficlight

# create logger
logger = logging.getLogger("sumo_logger")
logger.setLevel(logging.INFO)
# create console handler and set level to info
handler = logging.FileHandler(config.LOG_FILENAME)
# create formatter
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# add formatter to handler
handler.setFormatter(formatter)
# add handler to logger
logger.addHandler(handler)


def init_grid(simulation_bounds, cells_number):
    grid = list()
    width = simulation_bounds[1][0] / cells_number
    height = simulation_bounds[1][1] / cells_number
    for i in range(cells_number):
        for j in range(cells_number):
            # bounds coordinates for the area : (xmin, ymin, xmax, ymax)
            ar_bounds = ((i * width, j * height), (i * width, (j + 1) * height),
                         ((i + 1) * width, (j + 1) * height), ((i + 1) * width, j * height))
            area = Area(ar_bounds)
            area.name = 'Area ({},{})'.format(i, j)
            grid.append(area)
            traci.polygon.add(area.name, ar_bounds, (0, 255, 0))
    return grid

def get_all_lanes() -> List[Lane]:
    lanes = []
    for lane_id in traci.lane.getIDList():
        polygon_lane = LineString(traci.lane.getShape(lane_id))
        initial_max_speed = traci.lane.getMaxSpeed(lane_id)
        lanes.append(Lane(lane_id, polygon_lane, initial_max_speed))
    return lanes

def parsePhase(phase_repr):
    duration = search('duration: {:f}', phase_repr)
    minDuration = search('minDuration: {:f}', phase_repr)
    maxDuration = search('maxDuration: {:f}', phase_repr)
    phaseDef = search('phaseDef: {}\n', phase_repr)

    if phaseDef is None: phaseDef = ''
    else : phaseDef = phaseDef[0]

    return Phase(duration[0], minDuration[0], maxDuration[0], phaseDef)

def add_data_to_areas(areas: List[Area]):
    
    
    lanes = get_all_lanes()
    for area in areas:
        for lane in lanes:  # add lanes 
            if area.rectangle.intersects(lane.polygon):
                area.add_lane(lane)
                for tl_id in traci.trafficlight.getIDList():  # add traffic lights 
                    if lane.lane_id in traci.trafficlight.getControlledLanes(tl_id):
                        logics = []
                        for l in traci.trafficlight.getCompleteRedYellowGreenDefinition(tl_id): #add logics 
                            phases = []
                            for phase in traci.trafficlight.Logic.getPhases(l): #add phases to logics
                                phases.append(parsePhase(phase.__repr__()))
                            logics.append(Logic(l,phases)) 
                        area.add_tl(TrafficLight(tl_id,logics))

def compute_vehicle_emissions(veh_id):
    return (traci.vehicle.getCOEmission(veh_id)
            +traci.vehicle.getNOxEmission(veh_id)
            +traci.vehicle.getHCEmission(veh_id)
            +traci.vehicle.getPMxEmission(veh_id)
            +traci.vehicle.getCO2Emission(veh_id))


def get_all_vehicles() -> List[Vehicle]:
    vehicles = list()
    for veh_id in traci.vehicle.getIDList():
        veh_pos = traci.vehicle.getPosition(veh_id)
        vehicle = Vehicle(veh_id, veh_pos)
        vehicle.emissions = compute_vehicle_emissions(veh_id)
        vehicles.append(vehicle)
    return vehicles

def get_emissions(grid: List[Area], vehicles: List[Vehicle]):
    for area in grid:
        for vehicle in vehicles:
            if vehicle.pos in area:
                area.emissions += vehicle.emissions
        if area.emissions > config.EMISSIONS_THRESHOLD: 
            
            if config.limit_speed_mode and not area.limited_speed:
                logger.info(f'Action - Decrease of max speed into {area.name} by {config.speed_rf*100}%')
                actions.limit_speed_into_area(area, vehicles, config.speed_rf)
                traci.polygon.setColor(area.name, (255, 0, 0))
                traci.polygon.setFilled(area.name, True)
                if config.adjust_traffic_light_mode and not area.tls_adjusted:
                    logger.info(f'Action - Decrease of traffic lights duration by {config.trafficLights_duration_rf*100}%')
                    actions.adjust_traffic_light_phase_duration(area, config.trafficLights_duration_rf)
            
            if config.lock_area_mode and not area.locked:
                if actions.count_vehicles_in_area(area):
                    logger.info(f'Action - {area.name} blocked')
                    actions.lock_area(area)

def main():
    grid = list()
    try:
        traci.start(config.sumo_cmd)
        
        logger.info('Loading data for the simulation')
        start = time.perf_counter()
       
        grid = init_grid(traci.simulation.getNetBoundary(), config.CELLS_NUMBER)
        add_data_to_areas(grid)
        
        loading_time = round(time.perf_counter() - start,2)
        logger.info(f'Data loaded ({loading_time}s)')
        
        logger.info('Start of the simulation')
        step = 0 
        while step < config.n_steps : #traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()

            vehicles = get_all_vehicles()
            get_emissions(grid, vehicles)

            if config.weight_routing_mode:
                logger.info('Action - Lane weights adjusted')
                actions.adjust_edges_weights()

            step += 1
            
    finally:
        traci.close(False)
        logger.info('End of the simulation')
        total_emissions = 0
        for area in grid:
            total_emissions += area.emissions
                 
        logger.info(f'Total emissions = {total_emissions} mg')
        
        ref = config.get_basics_emissions()
        diff_with_actions = (ref - total_emissions)/ref
            
        logger.info(f'Reduction percentage of emissions = {diff_with_actions*100} %')
        logger.info('With the configuration : \n' + str(config.showConfig()))
        logger.info('Logs END')

        
if __name__ == '__main__':
    main()
