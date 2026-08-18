#!/bin/bash

#SBATCH --job-name=nanoproof-rl
#SBATCH --nodes=1
#SBATCH --cpus-per-task=56
#SBATCH --gpus=8
#SBATCH --mem=256G
#SBATCH --time=3-00:00:00
#SBATCH --partition=small-g
#SBATCH --account=project_465001752
#SBATCH -o out/%j-%x.out

singularity exec \
  --env OMP_NUM_THREADS=8 \
  --env HYDRA_FULL_ERROR=1 \
  -B /scratch/project_465001752 \
  -B /project/project_465001752 \
  /project/project_465001752/nanoproof-container.sif \
  torchrun \
    --nproc-per-node=8 \
    --rdzv_backend=c10d \
    --rdzv_endpoint=localhost:0 \
    --nnodes=1 \
    pretrain.py \
      'arch=trm' \
      'data_paths=["/project/project_465001752/trm/data/arc1concept-aug-1000"]' \
      '+checkpoint_path="/project/project_465001752/trm/checkpoints"' \
      'arch.L_layers=2' \
      'arch.H_cycles=3' \
      'arch.L_cycles=4' \
      '+run_name="arc1_baseline"' \
      '+project_name="TRM-ARC1"' \
      'ema=True' \
      "$@"

