from threading import Thread
from config import slices_db_url, ima_mgm_url, metrics_db_url, query_header, yaml_header, json_header
import requests, logging, time, yaml
import slice_resource_orchestrator as SRO
from ast import literal_eval

logger = logging.getLogger('Necos.SRO')

agents = {}
triggering = False

def update_vdus(slice_id, slice_part_id, node_type):
    r = requests.get(f'{slices_db_url}/{slice_id}/{slice_part_id}/vdus')
    if r.status_code == 200:
        vdus = r.json()
        nodes = [v['id'] for v in vdus['vdus'] if v['type']==node_type]
    else:
        nodes = []
    return nodes

def start_agent(slice_id, slice_part_id, policy):
    global triggering
    condition = '{}' + f' {policy["operator"]} {policy["value"]}'
    e_type = policy['type']
    node_type = policy['metric-collector']['node-type']
    metric = policy['metric-collector']['metric-name']
    if policy['metric-collector']['granularity'] == 'default':
        r = requests.get(f'{slices_db_url}/{slice_id}/{slice_part_id}/monitor/granularity')
        if r.status_code == 200:
            granularity = r.json()['granularity']
    else:
        granularity = policy['metric-collector']['granularity']
    nodes = update_vdus(slice_id, slice_part_id, node_type)
    trigger, value_t = next(iter(policy['trigger'].keys())), next(iter(policy['trigger'].values())) 
    agent_name = policy["name"]
    logger.info(f'[*] Elasticity agent \'{agent_name}\' started.')
    if trigger == 'points':
        analize = analize_consecutive_points
    elif trigger == 'time-window':
        analize = analize_time_window
    while True:
        if agents[slice_id][slice_part_id][agent_name]['status'] == 'running':
            nodes = update_vdus(slice_id, slice_part_id, node_type)
            if analize(value_t, metric, condition, nodes, agent_name):
                triggering = True
                # stop elasticity agents
                logger.info(f'   - Pausing elasticity agents...')
                for slice_id, value in agents.items():
                    for part_id, value2 in value.items():
                        for agent, value3 in value2.items():
                            value3['status'] = 'paused'
                # performing elasticity
                last_node = nodes[[int(n.split('-')[2]) for n in nodes].index(max([int(n.split('-')[2]) for n in nodes]))]
                logger.info(f'[-] Performing "Vertical Elasticity": {e_type}')
                SRO.save_times(slice_id, f'[*] Vertical elasticity')
                elasticity_time = time.time()
                vertical_elasticity(e_type, slice_id, slice_part_id, last_node, policy['post-deployment'])
                # logger.info('------ Ommiting automatical vertical elasticity')
                SRO.save_times(slice_id, f'[*] Total time for vertical elasticity: {(time.time()-elasticity_time)*1000} ms.')
                # reenable elasticity agents
                wait_ea = 30 # minutes
                for i in range(wait_ea):
                    logger.info(f'   - Reactivating elasticity agents in {wait_ea - i} minutes...')
                    time.sleep(60)
                logger.info(f'   - Reactivating elasticity agents now...')
                for slice_id, value in agents.items():
                    for part_id, value2 in value.items():
                        for agent, value3 in value2.items():
                            value3['status'] = 'running'
                triggering = False
        elif agents[slice_id][slice_part_id][agent_name]['status'] == 'stopped':
            break
        elif agents[slice_id][slice_part_id][agent_name]['status'] == 'paused':
            pass
        time.sleep(granularity)

def vertical_elasticity(e_type, slice_id, slice_part_id, node, post_elasticity):
    errors_at_db, errors_at_ima = False, False
    # Find DC Controller
    controller_id = int(slice_part_id.split('-')[0])
    part_uuid = int(slice_part_id.split('-')[1])
    r = requests.get(f'{slices_db_url}/controller_pointer/{controller_id}')
    if r.status_code != 200:
        logger.error(f'    [X] Controller {controller_id} not found.')
    controller = r.json()['controller']
    # generate yaml to dc controller
    logger.info('    - Generating YAML for DC Controller')
    r = requests.get(f'{slices_db_url}/slices/{slice_id}')
    if r.status_code == 200:
        parts = r.json()['slice']['slice-parts']
    i = 0
    for pa in parts:
        if next(iter(pa.keys())) == 'dc-slice-part':
            p = pa['dc-slice-part']
            if (p['dc-slice-part-id']['slice-controller-id'] == controller_id) and (p['dc-slice-part-id']['slice-part-uuid'] == part_uuid):
                break
        i += 1
    part = parts[i]
    keys_to_remove = ['dc-slice-controller', 'cost', 'type']
    for key in keys_to_remove:
        part['dc-slice-part'].pop(key, '')
    monitor = part['dc-slice-part'].pop('monitoring-parameters')
    keys_to_remove = ['host-count', 'version', 'vim-federated', 'vim-shared']
    for key in keys_to_remove:
        part['dc-slice-part']['VIM'].pop(key, '')
    vdus = part['dc-slice-part']['VIM'].pop('vdus')
    part['dc-slice-part']['VIM']['vdus'] = []
    part_name = part['dc-slice-part']['name']
    namespace = monitor['namespace']
    i = 0
    for vdu in vdus:
        if vdu['vdu']['id'] == node:
            break
        i += 1
    vdu = vdus[i]
    new_worker_ip = '.'.join(vdu['vdu']['ip'].split('.')[0:-1]) + '.' + str(int(vdu['vdu']['ip'].split('.')[-1])+1)
    aux = node.split('-')
    aux[2] = str(int(node.split('-')[2])+1)
    new_worker_id = '-'.join(aux)
    vdu['vdu']['ip'] = new_worker_ip
    vdu['vdu']['id'] = new_worker_id
    vdu['vdu']['name'] = new_worker_id
    part['dc-slice-part']['VIM']['vdus'].append(vdu)
    yaml_stream = yaml.dump({'elasticity': {'type': e_type, 'slice': {'id': slice_id, 'slice-parts': [part]}}}, Dumper=yaml.CDumper, width=1000)
    SRO.save_file('elasticity_dc_controller.yaml', yaml_stream)
    # make the call, expecting 201 and yaml
    controller_url = f'http://{controller["ip"]}:{controller["port"]}'
    logger.info(f'    - Calling DC Controller at {controller_url}')
    dc_time = time.time()
    r = requests.post(f'{controller_url}/slice_part/add_vm', headers = yaml_header, data = yaml_stream)
    SRO.save_times(slice_id, f'   - DC Controller: {(time.time()-dc_time)*1000} ms.')
    if (r.status_code != 201) and (r.status_code != 200):
        logger.error(f'[X] An error ocurred in the DC Controller. Aborting elasticity...')
        return
    logger.info(f'   - New worker deployed.')
    # Calling Slices Database
    logger.info('    - Calling Slices Database')
    db_time = time.time()
    r = requests.post(f'{slices_db_url}/{slice_id}/{part_name}/vdu', headers = json_header, json = vdu)
    SRO.save_times(slice_id, f'   - Slices Database: {(time.time()-db_time)*1000} ms.')
    if r.status_code != 201:
        logger.error(f'    [X] An error ocurred while saving the new worker')
        errors_at_db = True
    else:
        logger.info('    - Slice updated with the new VDU.')
    # Generate yaml for IMA.management
    logger.info('    - Generating YAML for IMA.management')
    _vdu = {'name': post_elasticity["node"], 'VIM': part["dc-slice-part"]["VIM"]["name"], 'namespace': namespace, 'commands': post_elasticity["commands"]}
    yaml_stream = yaml.dump({'slices': {'sliced': {'id': slice_id, 'slice-parts': [{"dc-slice-part": None, "name": part_name, 'vdus': [_vdu]}]}}}, Dumper=yaml.CDumper, width=1000)
    SRO.save_file('elasticity_ima_mgm.yaml', yaml_stream)
    # make the call expecting 201 and success JSON
    logger.info('    - Waiting 1 minute to redeploy the service...')
    time.sleep(60)
    logger.info('    - Calling IMA.management to redeploy the service')
    ima_time = time.time()
    r = requests.post(f'{ima_mgm_url}/deploy_service', headers = yaml_header, data = yaml_stream)
    SRO.save_times(slice_id, f'   - IMA.management: {(time.time()-ima_time)*1000} ms.')
    try:
        message = r.json()
        if type(message) is str:
            message = literal_eval(message)
    except:
        message = r.text
    logger.info(f'    - Response | Status code[{r.status_code}]: {str(message)[0:100]}...')
    if (r.status_code != 201) and (r.status_code != 200):
        logger.error(f'    [X] An error ocurred at IMA.management')
        errors_at_ima = True
    else:
        logger.info('    - Service redeployed correctly.')
    if errors_at_db or errors_at_ima:
        logger.warning(f'[!] Vertical elasticity finished with errors.')
    else:
        logger.info('[-] Vertical elasticity correctly executed.')

def analize_consecutive_points(consecutive_points, metric, condition, nodes, agent_name):
    global triggering
    query = f'SELECT * FROM \"{metric}\" WHERE "ResourceID" = $value GROUP BY * ORDER BY DESC LIMIT {consecutive_points}'
    for node in nodes:
        t_params = '{\"value\":\"' + node + '\"}'
        params = {'q': query, 'params': t_params}
        r = requests.get(metrics_db_url, headers=query_header, params=params)
        try:
            values = r.json()["results"][0]["series"][0]["values"]
            if sum([1 if eval(condition.format(value)) else 0 for time_h, value in values]) == consecutive_points:
                if not triggering:
                    operators = {'>': 'over', '<': 'under', '=': 'equal to', '>=': 'over or equal to', '<=': 'under or equal to'}
                    location = operators[condition.split()[1]]
                    logger.warning(f'[!] Elasticity agent \'{agent_name}\' | {node} is {location} threshold: {[str(value)[0:5] for time_h, value in values]}')
                    return True
                return False
            logger.info(f' + Elasticity agent \'{agent_name}\' | {node} - points: {[str(value)[0:5] for time_h, value in values]}')
        except:
            logger.info(f' + Elasticity agent \'{agent_name}\' | {node} - there is no data yet.')

def analize_time_window(seconds, metric, condition, nodes, agent_name):
    # curl -G http://necos-ima:8086/query?db=E2E_SLICE --data-urlencode 
    # "q=SELECT * FROM \"PERCENT_CPU_UTILIZATION\" WHERE \"ResourceID\" = 'k8s-node-1-5-104' AND time > now() - 40m GROUP BY * ORDER BY DESC"
    global triggering
    query = f'SELECT * FROM \"{metric}\" WHERE "ResourceID" = $value AND time > now() - {seconds}s GROUP BY * ORDER BY DESC'
    for node in nodes:
        t_params = '{\"value\":\"' + node + '\"}'
        params = {'q': query, 'params': t_params}
        r = requests.get(metrics_db_url, headers=query_header, params=params)
        try:
            values = r.json()["results"][0]["series"][0]["values"]
            if sum([1 if eval(condition.format(value)) else 0 for time_h, value in values]) == len(values):
                if not triggering:
                    operators = {'>': 'over', '<': 'under', '=': 'equal to', '>=': 'over or equal to', '<=': 'under or equal to'}
                    location = operators[condition.split()[1]]
                    logger.warning(f'[!] Elasticity agent \'{agent_name}\' | {node} is {location} threshold: {[str(value)[0:5] for time_h, value in values]}')
                    return True
                return False
            logger.info(f' + Elasticity agent \'{agent_name}\' | {node} - values: {[str(value)[0:5] for time_h, value in values]}')
        except:
            logger.info(f' + Elasticity agent \'{agent_name}\' | {node} - there is no data yet.')

def launch_elasticity_agents(slice_id):
    global agents
    r = requests.get(f'{slices_db_url}/slice_part_ids/{slice_id}/dc')
    if r.status_code != 200:
        return 404, r.json()
    part_ids = r.json()['slice_part_ids']
    for part_id in part_ids:
        r = requests.get(f'{slices_db_url}/{slice_id}/{part_id}/elasticity')
        if r.status_code == 200:
            try:
                policy = r.json()
                if type(policy) is str:
                    policy = literal_eval(policy)
                if type(policy) != dict:
                    raise Exception('Unexpected response')
            except:
                policy = r.text
                logger.warning(f'Unexpected response: {policy}')
                continue
            agents.setdefault(slice_id, {})
            agents[slice_id].setdefault(part_id, {})
            agents[slice_id][part_id].setdefault(policy['name'], {'status': 'running','thread': Thread(target=start_agent, kwargs={'slice_id': slice_id, 'slice_part_id': part_id, 'policy': policy})})
            agents[slice_id][part_id][policy['name']]['thread'].start()

def start(slice_id):
    Thread(target=launch_elasticity_agents, args=(slice_id,)).start()