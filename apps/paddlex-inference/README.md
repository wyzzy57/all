# PaddleX inference runtime

This image serves an exported static inference bundle through PaddleX and
Paddle Inference. It does not clone or install the PaddleDetection training
repository at image build or container startup time.

The bundle is mounted read-only and selected with `VISIOX_MODEL_DIR`. Training
plugin sources belong to the training worker image and are intentionally not
part of this inference runtime.
