#!/usr/bin/env bash

rsync \
   -av --delete \
   --progress \
   --exclude='*.egg-info/' --exclude='*.pyc' --exclude='__pycache__/' --exclude='*.pth' --exclude='target' --exclude='node_modules' \
   README.md lumi/jobs openprover scripts examples tests pyproject.toml README.md DOCS.md \
   lumi:~/openprover/
