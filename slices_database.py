from neo4j import GraphDatabase
import os, sys, yaml, time, json, logging
import db_tools, config

logger = logging.getLogger('Necos.SRO')

def open_conn():
    return GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_pwd))

def close_conn(driver):
    driver.close()

def store_slice(content):
    logger.info('Saving slice...')
    try:
        slice_parts = content.pop('slice-parts', [])
        slice_data = db_tools.extract_slice_from_json(content)
        if not slice_data:
            raise Exception('Slice data could not be extracted.')
    except Exception as e:
        logger.error(f'This YAML file does not have the correct fields. {e}')
        return 400, 'This YAML file does not have the correct fields.'
    _id = slice_data['id']
    is_unique = verify_unique(_id, 'Slice')
    if not is_unique:
        logger.error('This id is in use by other Slice.')
        return 409, 'This id is in use by other Slice.'
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            result = session.run(f'CREATE (sl:Slice {db_tools.get_parameters(slice_data)}) '
            'RETURN sl.id as id, id(sl) as internal_id').single().data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'The Slice could not be saved. {e}')
        return 400, f'The Slice could not be saved. {e}'
    ids = []
    try:
        internal_id = result['internal_id']
        slice_id = result['id']
        logger.info(f'Slice. int_id: {internal_id}, slice_id: {slice_id}')
        ids.append(internal_id)
    except Exception as e:
        logger.error(f'The Slice could not be saved. {e}')
        return 500, f'The Slice could not be saved. {e}'
    slice_part_ids = []
    for slice_part in slice_parts:
        part_type, part_description = next(iter(slice_part.items()))
        if part_type == 'dc-slice-part':
            result = store_dc_slice_part(part_description)
        elif part_type == 'net-slice-part':
            result = store_net_slice_part(part_description)
        if result[0] == 201:
            result, slice_part_id, part_ids = result
        else:
            rollback(ids)
            return result
        ids += part_ids
        ids.append(slice_part_id)
        slice_part_ids.append(slice_part_id)
    logger.info('Creating internal slice relationships')
    slice_part_match = ''
    slice_rel_slice_part = ''
    i = 1
    for slice_part_id in slice_part_ids:
        slice_part_match += f'MATCH (sp{i}:SlicePart) WHERE id(sp{i}) = {slice_part_id} '
        slice_rel_slice_part += f'CREATE (sp{i})<-[:EMPLOYS]-(sl) '
        i += 1
    try:
        slices_db = open_conn()
        slice_internal_id = ids[0]
        with slices_db.session() as session:
            session.run(f'MATCH (sl:Slice) WHERE id(sl) = {slice_internal_id} '
                + slice_part_match + slice_rel_slice_part)
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'Relationships could not be established. {e}')
        rollback(ids)
        return 500, 'Relationships could not be established.'
    return 201, get_slice(slice_id)[1]

def store_dc_slice_part(slice_part):
    logger.info('Saving Slice Part (DC)...')
    try:
        VIM = slice_part.pop('VIM')
        controller = slice_part.pop('dc-slice-controller')
        monitor = slice_part.pop('monitoring-parameters')
        slice_part_data = db_tools.extract_dc_part_from_json(slice_part)
    except Exception as e:
        logger.error(f'This YAML file does not have the correct fields. {e}')
        return 400, 'This YAML file does not have the correct fields.'
    vim_id, ids = store_vim(VIM)
    if vim_id and ids:
        ids.append(vim_id)
    else:
        logger.error('A VIM could not be saved. Undoing changes...')
        rollback(ids)
        return 500, 'A VIM could not be saved.'
    controller_id = store_controller(controller)
    if controller_id:
        ids.append(controller_id)
    else:
        logger.error('A Controller could not be saved. Undoing changes...')
        rollback(ids)
        return 500, 'A controller could not be saved.'
    monitor_id = store_monitor(monitor)
    if monitor_id:
        ids.append(monitor_id)
    else:
        logger.error('A Monitor could not be saved. Undoing changes...')
        rollback(ids)
        return 500, 'Some monitoring data could not be saved.'
    try:
        slices_db = open_conn()
        _id = slice_part_data['id']
        with slices_db.session() as session:
            result = session.run(F'CREATE (sp:SlicePart  {db_tools.get_parameters(slice_part_data)}) '
            'RETURN id(sp) as internal_id').single().data()
        close_conn(slices_db)
        internal_id = result['internal_id']
    except Exception as e:
        logger.error(f'The slice part could not be saved. {e}')
        rollback(ids)
        return 500, 'The slice part could not be saved.'
    try:
        logger.info('Creating internal slice part relationships...')
        slices_db = open_conn()
        with slices_db.session() as session:
            result = session.run(f'MATCH (sp:SlicePart) WHERE id(sp) = {internal_id} '
                f'MATCH (v:VIM) WHERE id(v) = {vim_id} '
                f'MATCH (c:Controller) WHERE id(c) = {controller_id} '
                f'MATCH (m:Monitor) WHERE id(m) = {monitor_id} '
                'CREATE (sp)-[:USES]->(v) '
                'CREATE (c)-[:HANDLES]->(sp) '
                'CREATE (m)-[:MONITORS]->(sp) RETURN sp.id').single().data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'Relationships could not be established. {e}')
        return 500, 'Relationships could not be established.'
    return 201, internal_id, ids

def store_vim(vim):
    try:
        vim_ref = vim.pop('vim-ref')
        vim_credential = vim.pop('vim-credential')
        vim['ip-api'] = vim_ref['ip-api']
        vim['ip-ssh'] = vim_ref['ip-ssh']
        vim['port-api'] = vim_ref['port-api']
        vim['port-ssh'] = vim_ref['port-ssh']
        vim['user-ssh'] = vim_credential['user-ssh']
        vim['password-ssh'] = vim_credential['password-ssh']
        vswitch = vim.pop('vswitch')
        vim['vswitch-name'] = vswitch['bridge-name']
        vim['vswitch-type'] = vswitch['type']
        vdus = vim.pop('vdus')
        if vim['ip-api'] is None:
            vim['ip-api'] = 'null'
        if vim['port-api'] is None:
            vim['port-api'] = 'null'
        vdu_ids = []
        for vdu in vdus:
            vdu_id = store_vdu(vdu['vdu'])
            if vdu_id:
                vdu_ids.append(vdu_id)
            else:
                logger.error('A VDU could not be saved. Undoing changes...')
                rollback(vdu_ids)
                return 500, 'A VDU could not be saved.'
    except Exception as e:
        logger.error(f'The VIM section does not have the correct fields. {e}')
        return
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            vim_internal_id = session.run(f'CREATE (v:VIM {db_tools.get_parameters(vim)}) RETURN id(v) as internal_id').single().value()
        close_conn(slices_db)
        # return vim_internal_id, vdu_ids
    except Exception as e:
        logger.error(e)
        return
    try:
        logger.info('Creating internal VIM relationships...')
        vdu_match, vdu_create = '', ''
        i = 1
        for vdu_id in vdu_ids:
            vdu_match += f'MATCH (vd{i}:VDU) WHERE id(vd{i}) = {vdu_id} '
            vdu_create += f'CREATE (vd{i})<-[:DEPLOYS]-(v) '
            i += 1
        slices_db = open_conn()
        with slices_db.session() as session:
            result = session.run(f'MATCH (v:VIM) WHERE id(v) = {vim_internal_id} '
                + vdu_match  + vdu_create)
        close_conn(slices_db)
        return vim_internal_id, vdu_ids
    except Exception as e:
        logger.error(f'Relationships could not be established. {e}')
        return 500, 'Relationships could not be established.'

def store_vdu(vdu):
    if vdu['type'] is None:
        vdu['type'] = 'null'
    try:
        epa_attributes = vdu.pop('epa-attributes')
        for key, value in epa_attributes['host-epa'].items():
            if key == 'memory-mb':
                vdu[key] = db_tools.check_int(str(value))
            elif key == 'os-properties':
                for key2, value2 in value.items():
                    vdu[f'os-{key2}'] = value2
            else:
                vdu[key] = value
    except Exception as e:
        logger.error(f'The VDUs section does not have the correct fields. {e}')
        return
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            vdu_internal_id = session.run(f'CREATE (v:VDU {db_tools.get_parameters(vdu)}) RETURN id(v) as internal_id').single().value()
        close_conn(slices_db)
        return vdu_internal_id
    except Exception as e:
        logger.error(e)
        return

def store_new_vdu(slice_id, part_name, vdu):
    vdu = vdu.pop('vdu')
    if vdu['type'] is None:
        vdu['type'] = 'null'
    try:
        epa_attributes = vdu.pop('epa-attributes')
        for key, value in epa_attributes['host-epa'].items():
            if key == 'memory-mb':
                vdu[key] = db_tools.check_int(str(value))
            elif key == 'os-properties':
                for key2, value2 in value.items():
                    vdu[f'os-{key2}'] = value2
            else:
                vdu[key] = value
    except Exception as e:
        logger.error(f'The VDUs section does not have the correct fields. {e}')
        return
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            vdu_internal_id = session.run(f'CREATE (v:VDU {db_tools.get_parameters(vdu)}) RETURN id(v) as internal_id').single().value()
            session.run(f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.name=\'{part_name}\' '
            f'MATCH (sp)-[:USES]->(vim:VIM) MATCH (v:VDU) WHERE id(v) = {vdu_internal_id} CREATE (vim)-[:DEPLOYS]->(v)')
        close_conn(slices_db)
        return vdu_internal_id
    except Exception as e:
        logger.error(e)
        return

def store_controller(controller):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            controller_internal_id = session.run(
                'MERGE (c:Controller {controllerId: $_id}) SET c += {ip: $ip, port: $port} RETURN id(c) as internal_id',
                _id=db_tools.check_int(controller['controller-id']), ip=controller['ip'], port=db_tools.check_int(controller['port'])).single().value()
        close_conn(slices_db)
        return controller_internal_id
    except Exception as e:
        logger.error(e)
        return

def store_monitor(monitor):
    try:
        metrics = monitor.pop('metrics') 
        monitor['metrics'] = [metric['metric']['name'] for metric in metrics]
    except Exception as e:
        logger.error(f'The Monitor section does not have the correct fields. {e}')
        return
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            monitor_internal_id = session.run(f'CREATE (m:Monitor {db_tools.get_parameters(monitor)}) RETURN id(m) as internal_id').single().value()
        close_conn(slices_db)
        return monitor_internal_id
    except Exception as e:
        logger.error(e)
        return

def store_net_slice_part(slice_part):
    logger.info('Saving Slice Part (Network)...')
    try:
        WIM = slice_part.pop('WIM')
        controller = slice_part.pop('wan-slice-controller')
        slice_part_data = db_tools.extract_net_part_from_json(slice_part)
        link = slice_part_data.pop('link')
        dc1, dc2 = link.split(':')
    except Exception as e:
        logger.error(f'This YAML file does not have the correct fields. {e}')
        return 400, 'This YAML file does not have the correct fields.'
    ids = []
    wim_id = store_wim(WIM)
    if wim_id:
        ids.append(wim_id)
    else:
        logger.error('A WIM could not be saved. Undoing changes...')
        rollback(ids)
        return 500, 'A WIM could not be saved.'
    controller_id = store_controller(controller)
    if controller_id:
        ids.append(controller_id)
    else:
        logger.error('A Controller could not be saved. Undoing changes...')
        rollback(ids)
        return 500, 'A controller could not be saved.'
    try:
        slices_db = open_conn()
        _id = slice_part_data['id']
        with slices_db.session() as session:
            result = session.run(f'CREATE (sp:SlicePart {db_tools.get_parameters(slice_part_data)}) '
            'RETURN id(sp) as internal_id').single().data()
        close_conn(slices_db)
        internal_id = result['internal_id']
    except Exception as e:
        logger.error(f'The slice part could not be saved. {e}')
        rollback(ids)
        return 500, 'The slice part could not be saved.'
    try:
        logger.info('Creating internal slice part relationships...')
        slices_db = open_conn()
        with slices_db.session() as session:
            result = session.run(f'MATCH (sp:SlicePart) WHERE id(sp) = {internal_id} '
                f'MATCH (w:WIM) WHERE id(w) = {wim_id} '
                f'MATCH (c:Controller) WHERE id(c) = {controller_id} '
                f'MATCH (dc1:SlicePart) WHERE dc1.id = \'{dc1}\' '
                f'MATCH (dc2:SlicePart) WHERE dc2.id = \'{dc2}\' '
                'CREATE (sp)-[:USES]->(w) '
                'CREATE (c)-[:HANDLES]->(sp) '
                'CREATE (sp)-[:CONNECTS {part: 1}]->(dc1) '
                'CREATE (sp)-[:CONNECTS {part: 2}]->(dc2) '
                'RETURN sp.id').single().data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'Relationships could not be established. {e}')
        return 500, 'Relationships could not be established.'
    return 201, internal_id, ids

def store_wim(wim):
    try:
        wim_ref = wim.pop('wim-ref')
        if type(wim_ref) is dict:
            wim['ip'] = wim_ref['ip']
            wim['port'] = wim_ref['port']
        # if wim-ref is undefined, wim-ref is not stored
    except Exception as e:
        logger.error(f'The WIM section does not have the correct fields. {e}')
        return
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            wim_internal_id = session.run(f'CREATE (w:WIM {db_tools.get_parameters(wim)}) RETURN id(w) as internal_id').single().value()
        close_conn(slices_db)
        return wim_internal_id
    except Exception as e:
        logger.error(e)
        return

def slice_exists(slice_id):
    slices_db = open_conn()
    with slices_db.session() as session:
        result = session.run(f'MATCH (sl:Slice) WHERE sl.id="{slice_id}" RETURN COUNT(sl) > 0').single().value()
    close_conn(slices_db)
    if result:
        code = 200
    else:
        code = 404
    return code, {'Exists': result}

def get_slice_part_ids(slice_id, _type):
    #ATCH (sl:Slice) WHERE sl.id = 'IoTService_sliced' MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.type =~ '(?i)dc' or sp.type =~ "(?i)edge" return sp.id
    query = 'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) '
    if _type == 'dc':
        query += 'WHERE sp.type =~ "(?i)dc" or sp.type =~ "(?i)edge"'
    elif _type == 'net':
        query += 'WHERE sp.type =~ "(?i)net"'
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            slice_part_ids = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' '
                f'{query} RETURN sp.id as slice_part_id').data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'We could not get the slice. {e}')
        return 400, f'We could not get the slice. {e}'
    slice_part_ids = [slice_part['slice_part_id'] for slice_part in slice_part_ids]
    return 200, slice_part_ids

def get_controller_pointer(controller_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            controller_pointer = session.run(f'MATCH (c:Controller) WHERE c.controllerId = {controller_id} RETURN c.ip as ip, c.port as port').single().data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'We could not get the controller pointer. {e}')
        return 400, f'We could not get the controller pointer.'
    if controller_pointer:
       return 200, controller_pointer
    else:
        return 404, 'Pointer for this controller could not be found'

def get_slice(slice_id):
    logger.info('Returning slice details...')
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            Slice = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' RETURN properties(sl) as slice').single()
            slice_parts = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' '
                'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) RETURN properties(sp) as slicePart').data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'We could not get the slice. {e}')
        return 400, f'We could not get the slice. {e}'
    if not Slice:
        return 404, 'The requested slice does not exists.'
    Slice = {'slice': db_tools.set_slice_parameters_for_json(Slice['slice'])}
    slice_parts = [{'net-slice-part': slice_part['slicePart']} if slice_part['slicePart']['type'] == 'NET' else {'dc-slice-part': slice_part['slicePart']} for slice_part in slice_parts]
    slice_parts = [db_tools.set_part_parameters_for_json(part) for part in slice_parts]
    for part in slice_parts:
        key = next(iter(part.keys()))
        key2 = 'net-slice-part-id' if part[key]['type'] == 'NET' else 'dc-slice-part-id'
        part_id = '{}-{}'.format(part[key][key2]['slice-controller-id'], part[key][key2]['slice-part-uuid'])
        if key == 'net-slice-part':
            slices_db = open_conn()
            with slices_db.session() as session:
                elements = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                    'MATCH (sp)<-[:HANDLES]-(c:Controller) '
                    'MATCH (sp)-[:USES]->(w:WIM) RETURN properties(c) as Controller, properties(w) as WIM').single().data()
                connections = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                    'MATCH (sp)-[c:CONNECTS]->(osp:SlicePart) RETURN osp.id as DC_id, c.part as part').data()
            close_conn(slices_db)
            controller = db_tools.set_controller_parameters_for_json(elements['Controller'])
            part[key].update(controller)
            wim = db_tools.set_wim_parameters_for_json(elements['WIM'])
            part[key].update(wim)
            for connection in connections:
                dc_slice_controller_id, slice_part_uuid = [int(x) for x in connection['DC_id'].split('-')]
                if connection['part'] == 1:
                    part[key]['links'][0]['dc-part1'] = {'dc-slice-controller-id': dc_slice_controller_id, 'slice-part-uuid': slice_part_uuid}
                elif connection['part'] == 2:
                    part[key]['links'][1]['dc-part2'] = {'dc-slice-controller-id': dc_slice_controller_id, 'slice-part-uuid': slice_part_uuid}
        elif key == 'dc-slice-part':
            slices_db = open_conn()
            with slices_db.session() as session:
                elements = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                    'MATCH (sp)<-[:HANDLES]-(c:Controller) '
                    'MATCH (sp)-[:USES]->(v:VIM) '
                    'MATCH (sp)<-[:MONITORS]-(m:Monitor) '
                    'RETURN properties(c) as Controller, properties(v) as VIM, properties(m) as Monitor').single().data()
                vdus = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                    'MATCH (sp)-[:USES]->(v:VIM) '
                    'MATCH (v)-[:DEPLOYS]->(vd:VDU) RETURN properties(vd) as VDU').data()
            close_conn(slices_db)
            controller = db_tools.set_controller_parameters_for_json(elements['Controller'])
            part[key].update(controller)
            vim = db_tools.set_vim_parameters_for_json(elements['VIM'])
            part[key].update(vim)
            part[key]['VIM']['vdus'] = [db_tools.set_vdu_parameters_for_json(vdu['VDU']) for vdu in vdus]
            monitor = db_tools.set_monitor_parameters_for_json(elements['Monitor'])
            part[key].update(monitor)
    Slice['slice'].update({'slice-parts': slice_parts})
    return 200, Slice

def get_slices():
    logger.info('Returning all slices...')
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            ids = session.run(f'MATCH (sl:Slice) RETURN sl.id as id').data()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'We have problems to collect slices details. {e}')
        return 500, f'We have problems to collect slices details. {e}'
    slices = []
    for _id in ids:
        slices.append(get_slice(_id['id'])[1])
    return 200, slices

def register_controller(stream):
    logger.info('Registering controller.')
    try:
        content = yaml.load(stream, Loader=yaml.CLoader)
    except Exception as e:
        logger.error(f'Invalid YAML file: {e}')
        return 400, 'Invalid YAML file.'
    try:
        _id = content['controller']['controller-id']
        _type = content['controller']['type']
        if _type == 'DC':
            provider = 'DC ' + content['controller'].pop('name')
        elif _type == 'NET':
            provider = 'NET ' + content['controller'].pop('name')
    except Exception as e:
        logger.error(f'This YAML file does not have the correct fields: {e}')
        return 400, 'This YAML file does not have the correct fields.'
    is_unique = verify_unique(_id, 'Controller')
    if not is_unique:
        logger.error('This id is in use by other DC/WAN Slice Controller.')
        return 409, 'This id is in use by other DC/WAN Slice Controller.'
    slices_db = open_conn()
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            if _type == 'DC':
                result = session.run('CREATE (c:Controller {controllerId: $id, dcSliceProvider: $provider})', id=_id, provider=provider).single()
            elif _type == 'NET':
                result = session.run('CREATE (c:Controller {controllerId: $id, wanSliceProvider: $provider})', id=_id, provider=provider).single()
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'Could not register controller. {e}')
        return 500, f'Could not register controller. {e}'
    if result:
        logger.error(f'Unknown error. {result}')
        return 500, f'Unknown error. {result}'
    else:
        return 201, 'Controller stored successfully.'

def verify_unique(_id, node):
    if type(_id) is str:
        _id = f'\'{_id}\''
    slices_db = open_conn()
    with slices_db.session() as session:
        result = session.run(f'MATCH (n:{node}) WHERE n.id={_id} RETURN COUNT(n) > 0').single().value()
    close_conn(slices_db)
    return not result

def add_namespace(slice_id, part_name, namespace):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            session.run(f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' '
            f'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.name=\'{part_name}\' '
            'MATCH (sp)<-[:MONITORS]-(m:Monitor) '
            f'SET m.namespace=\'{namespace}\'')
        close_conn(slices_db)
        logger.info(f"Namespace '{namespace}' added to '{slice_id}.{part_name}'")
        return 201, f"Namespace '{namespace}' added to '{slice_id}.{part_name}'"
    except Exception as e:
        logger.error(f"Namespace '{namespace}' could not be added to '{slice_id}.{part_name}': {e}")
        return 500, f"Namespace '{namespace}' could not be added to '{slice_id}.{part_name}': {e}"

def get_vdus(slice_id, part_name):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            vdus = session.run(f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' '
            f'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.name=\'{part_name}\' '
            f'MATCH (sp)-[:USES]->(v:VIM) MATCH (v)-[:DEPLOYS]->(vd:VDU) '
            f'RETURN vd.id as id, vd.ip as ip, vd.type as type').data()
        close_conn(slices_db)
        return vdus
    except:
        return 404, f"VDUs not found"

def get_vdus_id(slice_id, part_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            query = f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.id=\'{part_id}\' MATCH (sp)-[:USES]->(v:VIM) MATCH (v)-[:DEPLOYS]->(vd:VDU) RETURN vd.id as id, vd.ip as ip, vd.type as type'
            vdus = session.run(query).data()
        close_conn(slices_db)
        return 200, vdus
    except:
        return 404, f"VDUs not found"

def get_monitor_granularity(slice_id, part_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            query = f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.id=\'{part_id}\' MATCH (m:Monitor)-[:MONITORS]->(sp) RETURN m.granularitySecs as granularity'
            granularity = session.run(query).single().data()
        close_conn(slices_db)
        return 200, granularity
    except:
        return 404, f"VDUs not found"

def add_elasticity_rule(slice_id, part_name, rule):
    trigger = rule.pop('trigger')
    rule['trigger'] = f'{{{next(iter(trigger.keys()))}: {next(iter(trigger.values()))}}}'
    metric_collector = rule.pop('metric-collector')
    rule['metric-name'] = metric_collector.pop('metric-name')
    rule['metric-node-type'] = metric_collector.pop('node-type')
    rule['metric-granularity'] = metric_collector.pop('granularity')
    post_deployment = rule.pop('post-deployment')
    # save rule
    rule_id = 0
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            rule_id = session.run(f'CREATE (r:ElasticityPolicy {db_tools.get_parameters(rule)}) RETURN id(r) as rule_id').single().value()
            if not rule_id:
                return 500, "Elasticity policy could not be added to the slice."
            session.run(f'MATCH (sl:Slice) WHERE sl.id=\'{slice_id}\' '
            f'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.name=\'{part_name}\' '
            f'MATCH (r:ElasticityPolicy) WHERE id(r)={rule_id} '
            f'CREATE (sp)<-[:APPLIES]-(r)')
        close_conn(slices_db)
        logger.info(f"Elasticity policy added to '{slice_id}.{part_name}'")
    except Exception as e:
        logger.error(f"Elasticity policy could not be added to '{slice_id}.{part_name}': {e}")
        return 500, f"Elasticity policy could not be added to '{slice_id}.{part_name}': {e}"
    for d in post_deployment:
        action = d['action']
        # add node to execute commands
        # [{'id': 'k8s-master11', 'ip': '10.10.5.1', 'type': 'master'}, {'id': 'k8s-node11', 'ip': '10.10.5.2', 'type': 'worker'}]
        vdus = get_vdus(slice_id, part_name)
        if action['node'] == 'master':
            action['node'] = [v['id'] for v in vdus if v['type']=='master'][0]
        elif action['node'] == 'new-node':
            # TO DO: add a function to add the new-worker if required
            pass
        else:
            # Explicit indicated node
            pass
        action['commands'] = str(action.pop('commands'))
        try:
            slices_db = open_conn()
            with slices_db.session() as session:
                action_id= session.run(f'CREATE (a:Action {db_tools.get_parameters(action)}) RETURN id(a) as action_id').single().value()
                if not rule_id:
                    return 500, "Action could not be added to the elasticity policy."
                session.run(f'MATCH (r:ElasticityPolicy) WHERE id(r)={rule_id} '
                f'MATCH (a:Action) WHERE id(a)={action_id} '
                f'CREATE (a)<-[:TRIGGERS]-(r)')
            close_conn(slices_db)
            logger.info(f"Action added to the elasticity policy.")
        except Exception as e:
            logger.error(f"Action could not be added to the elasticity policy.")
    return 201, f"Action added to the elasticity policy."

def get_elasticity_rule(slice_id, part_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            # TO DO: Change to more than 1 action by policy, and to get all policies 
            policy = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' '
                f'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                f'MATCH (r:ElasticityPolicy)-[:APPLIES]->(sp) '
                f'MATCH (r)-[:TRIGGERS]->(a:Action) RETURN properties(r) as policy, properties(a) as action').single().data()
        close_conn(slices_db)
        if policy:
            policy = db_tools.set_elasticity_parameters_for_json(policy)
            return 200, policy
        else:
            return 404, 'Policy not found'
    except Exception as e:
        logger.info(f'There is not a policy in this slice part. {e}')
        return 404, f'There is not a policy in this slice part. {e}'

def delete_slice(slice_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            # deleting slice-slicepart relationships
            slice_parts = session.run(f'MATCH (sl:Slice) WHERE sl.id = "{slice_id}" '
                'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) DETACH DELETE sl RETURN sp.id as sp_id').value()
            # delete slicepart-controller relationships
            for part in slice_parts:
                session.run(f'MATCH (sp:SlicePart) WHERE sp.id = "{part}" '
                    'MATCH (c:Controller)-[h:HANDLES]->(sp) DELETE h')
                session.run(f'MATCH (sp:SlicePart) WHERE sp.id = "{part}" '
                        'MATCH (sp)<-[:APPLIES]-(r:ElasticityPolicy) '
                        'MATCH (a:Action)<-[:TRIGGERS]-(r) '
                        'DETACH DELETE a, r')
            # delete slicepart-vim relationships
            vims = []
            for part in slice_parts:
                try:
                    vim = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = "{part}" '
                        'MATCH (sp)-[u:USES]->(v:VIM) DELETE u RETURN id(v) as vim_id').single().value()
                    vims.append(vim)
                except:
                    pass
            # delete vim subgraph
            for vim in vims:
                session.run(f'MATCH (v:VIM) WHERE id(v) = {vim} '
                    'MATCH (v)-[r]-(n) DELETE r, n, v')
            # delete net connections
            for part in slice_parts:
                session.run(f'MATCH (sp:SlicePart) WHERE sp.id = "{part}" '
                    'MATCH (sp)-[c:CONNECTS]-(n) DELETE c')
            # delete slice parts subgraphs
            for part in slice_parts:
                session.run(f'MATCH (sp:SlicePart) WHERE sp.id = "{part}" '
                    'MATCH (sp)-[r]-(n) DELETE r, n, sp')
        close_conn(slices_db)
    except Exception as e:
        logger.error(f'The slice "{slice_id}" could not be deleted. {e}')
        return 500, f'The slice "{slice_id}" could not be deleted. {e}'
    return 200, f'The slice \'{slice_id}\' was successfully deleted.'

def rollback(ids):
    pass

def aux_node():
    logger.info('Creating auxiliar node...')
    while True:
        slices_db = open_conn()
        with slices_db.session() as session:
            _id = session.run('CREATE (a:Aux {name: $name}) RETURN id(a)', name='aux').single().value()
        close_conn(slices_db)
        if _id == 0:
            return 201, 'Auxiliar node created'
        else:
            slices_db = open_conn()
            with slices_db.session() as session:
                session.run(f'MATCH (a:Aux) WHERE id(a)={_id} DELETE a')
            close_conn(slices_db)