#!/bin/bash

./sdb-rest.py &
./sro-rest.py &

trap ctrl_c INT

function ctrl_c() {
    curl http://localhost:5005/bye
    curl http://localhost:5006/bye
    T_LOG="$(date +"%Y-%m-%d-%H-%M-%S")"
    SRO_LOG="${T_LOG}-SRO.log"
    SDB_LOG="${T_LOG}-SDB.log"
    mv logs/SRO.log logs/$SRO_LOG
    mv logs/SDB.log logs/$SDB_LOG
    exit
}

while true; do
    sleep 30
done
