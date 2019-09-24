# SRO CONFIGURATION
sro_port = 5005

# SLICES DATABASE CONFIGURATION
slices_db_ip_addr = 'localhost'
slices_db_port = 5006
slices_db_url = f'http://{slices_db_ip_addr}:{slices_db_port}/necos/db'
neo4j_bolt_port = 7687
neo4j_uri = f'bolt://{slices_db_ip_addr}:{neo4j_bolt_port}'
neo4j_user = 'neo4j'
neo4j_pwd = 'admin'

# IMA CONFIGURATION
ima_ip_addr = 'necos-ima'
ima_mon_port = 4567
ima_mgm_port = 5001
# ima_ip_addr = 'localhost'
# ima_mon_port = 5000
# ima_mgm_port = 5000
ima_mon_url = f'http://{ima_ip_addr}:{ima_mon_port}/necos/ima'
ima_mgm_url = f'http://{ima_ip_addr}:{ima_mgm_port}/necos/ima'
influxdb_port = 8086
influxdb_name = 'E2E_SLICE'
metrics_db_url = f'http://{ima_ip_addr}:{influxdb_port}/query?db={influxdb_name}'

# HTTP HEADERS
json_header = {'Content-Type': 'application/json'}
query_header = {"Accept-Encoding":"gzip"}
yaml_header = {'Content-type': 'text/x-yaml'}
text_header = {'Content-Type': 'text/plain'}

