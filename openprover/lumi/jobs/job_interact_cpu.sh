#!/bin/bash

# https://docs.lumi-supercomputer.eu/runjobs/scheduled-jobs/container-jobs

srun \
  --job-name interact_cpu \
  --cpus-per-task 64 \
  --mem=128G \
  --nodes=1 \
  --partition=small \
  --account=project_465001752 \
  --time=04:00:00 \
  --pty \
  env \
    SINGULARITYENV_ELAN_HOME=/project/project_465001752/.cache/elan \
    SINGULARITYENV_LAKE_HOME=/project/project_465001752/.cache/lake \
  singularity shell -B /project/project_465001752 -B /scratch/project_465001752 -B /flash/project_465001752 /project/project_465002619/nanoproof-container.sif
