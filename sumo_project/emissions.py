import argparse
import sys
import time
from traci import trafficlight
import traci
from typing import List

from parse import search
from shapely.geometry import LineString

import actions
from config import Config
from model import Area, Vehicle, Lane , TrafficLight , Phase , Logic


def init_grid(simulation_bounds, areas_number):
    grid = list()
    width = simulation_bounds[1][0] / areas_number
    height = simulation_bounds[1][1] / areas_number
    for i in range(areas_number):
        for j in range(areas_number):
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


def parse_phase(phase_repr):
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
                        for l in traci.trafficlight.getCompleteRedYellowGreenDefinition(tl_id):  # add logics 
                            phases = []
                            for phase in traci.trafficlight.Logic.getPhases(l):  # add phases to logics
                                phases.append(parse_phase(phase.__repr__()))
                            logics.append(Logic(l, phases)) 
                        area.add_tl(TrafficLight(tl_id, logics))


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


def get_emissions(grid: List[Area], vehicles: List[Vehicle], current_step, config, logger):
    for area in grid:
        vehicle_emissions = 0
        for vehicle in vehicles:
            if vehicle.pos in area:
                vehicle_emissions += vehicle.emissions
                
        area.emissions_by_step.append(vehicle_emissions)
        
        if area.sum_emissions_into_window(current_step, config.window_size) >= config.emissions_threshold: 
                
            if config.limit_speed_mode and not area.limited_speed:
                logger.info(f'Action - Decreased max speed into {area.name} by {config.speed_rf*100}%')
                actions.limit_speed_into_area(area, vehicles, config.speed_rf)
                if config.adjust_traffic_light_mode and not area.tls_adjusted:
                    logger.info(f'Action - Decreased traffic lights duration by {config.trafficLights_duration_rf*100}%')
                    actions.adjust_traffic_light_phase_duration(area, config.trafficLights_duration_rf)
                
            if config.lock_area_mode and not area.locked:
                if actions.count_vehicles_in_area(area):
                    logger.info(f'Action - {area.name} blocked')
                    actions.lock_area(area)
                    
            traci.polygon.setColor(area.name, (255, 0, 0))
            traci.polygon.setFilled(area.name, True)
        
        else:
            actions.reverse_actions(area)


def run(config, logger):
    grid = list()
    try:
        traci.start(config.sumo_cmd)
        logger.info(f'Loaded simulation file : {config._SUMOCFG}')
        logger.info('Loading data for the simulation')
        start = time.perf_counter()
       
        grid = init_grid(traci.simulation.getNetBoundary(), config.areas_number)
        add_data_to_areas(grid)
        
        loading_time = round(time.perf_counter() - start, 2)
        logger.info(f'Data loaded ({loading_time}s)')
        
        logger.info('Start of the simulation')
        step = 0 
        while step < config.n_steps :  # traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()

            vehicles = get_all_vehicles()
            get_emissions(grid, vehicles, step, config, logger)

            if config.weight_routing_mode:
                actions.adjust_edges_weights()

            step += 1
            
    finally:
        traci.close(False)
        simulation_time = round(time.perf_counter() - start, 2)
        logger.info(f'End of the simulation ({simulation_time}s)')
        
        total_emissions = 0
        for area in grid:
            total_emissions += area.sum_all_emissions()
    
        logger.info(f'Total emissions = {total_emissions} mg')
        
        if not config.without_actions_mode :
            ref = config.get_basics_emissions()
            if not (ref is None):
                diff_with_actions = (ref - total_emissions) / ref    
                logger.info(f'Reduction percentage of emissions = {diff_with_actions*100} %')
    
    
def main(args):
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-f", "--configfile", type=str, default='configs/default_config.json', required=False)
    parser.add_argument("-save", "--save", action="store_true")
    parser.add_argument("-ref", "--ref", action="store_true")
    args = parser.parse_args(args)
    
    # > py ./emissions.py -f configs/config1.json -save
    # will load the configuration file "config1.json" and save logs into the logs directory 
    
    # > py ./emissions.py -f configs/config1.json -save -ref & py ./emissions.py -f configs/config1.json -save
    # same as above but also launches a reference simulation by using -ref option 
    
    config = Config()
    config.import_config_file(args.configfile)
    config.init_traci()
    logger = config.init_logger(save_logs=args.save)
    if args.ref: 
        config.without_actions_mode = True
        config.check_config()
        logger.info(f'Reference simulation')
    logger.info(f'Loaded configuration file : {args.configfile}')
    
    run(config, logger)

            
if __name__ == '__main__':
    main(sys.argv[1:])
