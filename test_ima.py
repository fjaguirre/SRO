#!/usr/bin/python3
from flask import Flask, request, jsonify, make_response
import yaml

app = Flask(__name__)

# Example for endpoint consumed by SRO
# to register pointers of slice and monitor parts

@app.route('/necos/ima/start_monitoring', methods=['POST'])
def start_monitoring():
    try:
        Slice = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nStart Monitoring')
    print(Slice)
    return jsonify({'Slice': 'Is now monitored'}), 201

@app.route('/necos/ima/start_management', methods=['POST'])
def start_management():
    try:
        Slice = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nStart Management')
    print(Slice)
    res = {'IoTService_sliced':{'dc-slice2': 'exists'}}
    return jsonify(res), 200

@app.route('/necos/ima/start_container_monitoring', methods=['POST'])
def service_monitoring():
    try:
        Slice = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nRight part')
    print(Slice)
    return jsonify({'result': True}), 201

@app.route('/necos/ima/deploy_service', methods=['POST'])
def service_deployment():
    try:
        content = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nLeft part')
    print(content)
    return jsonify({'result': True}), 200

@app.route('/necos/ima/delete_slice', methods=['DELETE'])
def delete_slice():
    try:
        content = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nRight part')
    print(content)
    return jsonify({'result': 'Slice Deleted'}), 200

@app.route('/necos/ima/slice_part', methods=['DELETE'])
def delete_slice_c():
    try:
        content = yaml.load(request.data, Loader=yaml.CLoader)
    except Exception as e:
        return jsonify({'Error': f'Invalid YAML. {e}'}), 400
    print('\nController')
    print(content)
    return jsonify({'Controller': 'Slice Part Deleted'}), 200

@app.route('/necos/ima/stopManagement', methods=['POST'])
def stop_management():
    content = request.data.decode('utf-8')
    print('\nLeft part')
    print(content)
    return jsonify({'result': 'Management stopped'}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)