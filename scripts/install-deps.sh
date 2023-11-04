#!/bin/bash

set -o errexit

# Install Python .venv
python3.10 -m venv .venv --upgrade-deps
. .venv/bin/activate

# Install AWS CDK Toolkit locally
npm install

# Install project dependencies
pip install '.[dev]'

# install pre-commit hooks
pre-commit install
