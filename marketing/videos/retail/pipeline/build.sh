#!/bin/bash
# Build a fresh local demo from the worktree ../retail-src (origin/develop): ./build.sh   (remove env/ first for a clean rebuild)
cd "$(dirname "$0")"; source ./env.sh
CK=$(cd .. && pwd)/retail-src; PY=/home/user/venv/bin/python
mkdir -p env; cd $CK
$PY manage.py collectstatic --noinput >/dev/null 2>&1 && echo static ok
$PY manage.py migrate --noinput >/dev/null 2>&1 && echo migrate ok
$PY manage.py prepare_demo 2>&1 | tail -2
$PY manage.py prepare_demo_fresh --force 2>&1 | tail -2
$PY manage.py prepare_demo_templates --force --only commercial:grocery 2>&1 | tail -3
