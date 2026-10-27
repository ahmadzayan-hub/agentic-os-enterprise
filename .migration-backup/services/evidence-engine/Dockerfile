# Kept as a pointer. The control plane is built from the repository root so
# that the API, the worker and the CLIs share one image to scan and sign:
#
#   docker build -f infrastructure/docker/Dockerfile --target base .
#
# Building this file directly would produce a second image with the same code
# and a different dependency closure, which is exactly the drift that makes a
# supply chain hard to attest. It is intentionally not a working build.
FROM scratch
LABEL org.opencontainers.image.description="Superseded: build infrastructure/docker/Dockerfile --target base"
