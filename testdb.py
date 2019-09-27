from neo4j import GraphDatabase
import os, sys, yaml, time, json, logging
import db_tools, config

def open_conn():
    return GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_pwd))

def close_conn(driver):
    driver.close()

def get_slice(slice_id):
    try:
        slices_db = open_conn()
        with slices_db.session() as session:
            Slice = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' RETURN properties(sl) as slice').single()
            slice_parts = session.run(f'MATCH (sl:Slice) WHERE sl.id = \'{slice_id}\' '
                'MATCH (sl)-[:EMPLOYS]->(sp:SlicePart) RETURN properties(sp) as slicePart').data()
        close_conn(slices_db)
    except Exception as e:
        print(f'We could not get the slice. {e}')
        return
    if not Slice:
        print('The requested slice does not exists.')
        return
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
                policies = session.run(f'MATCH (sp:SlicePart) WHERE sp.id = \'{part_id}\' '
                    'MATCH (sp)<-[:APPLIES]-(e:ElasticityPolicy) '
                    'RETURN properties(e) as policy').data()
                if policies:
                    elasticity = {'elasticity': []}
                    print(policies)
                    for policy in policies:
                        
                        actions = session.run(f'MATCH (e:ElasticityPolicy) WHERE e.name = \'{policy["policy"]["name"]}\' '
                            'MATCH (e)-[:TRIGGERS]->(a:Action) '
                            'RETURN properties(a) as action').data()
                print()
            close_conn(slices_db)
            controller = db_tools.set_controller_parameters_for_json(elements['Controller'])
            part[key].update(controller)
            vim = db_tools.set_vim_parameters_for_json(elements['VIM'])
            part[key].update(vim)
            part[key]['VIM']['vdus'] = [db_tools.set_vdu_parameters_for_json(vdu['VDU']) for vdu in vdus]
            monitor = db_tools.set_monitor_parameters_for_json(elements['Monitor'])
            part[key].update(monitor)
    Slice['slice'].update({'slice-parts': slice_parts})
    return Slice

print(get_slice('IoTService_sliced'))