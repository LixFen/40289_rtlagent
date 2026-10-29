# Official platform-only image. Organizers rebuild this Dockerfile in fallback mode.
ARG BASE_IMAGE=fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56
FROM ${BASE_IMAGE} AS slang-build
WORKDIR /slang-build
COPY tools/install_slang.py ./tools/install_slang.py
COPY serve/slang-release.json ./serve/slang-release.json
COPY vendor/slang-linux-v12.0.tar.gz /slang-build/slang.tar.gz
RUN python3 tools/install_slang.py slang.tar.gz --prefix /opt/slang/12.0 \
    && /opt/slang/12.0/bin/slang --version \
    && printf 'module TopModule(input logic a, output logic q); assign q=a; endmodule\n' > smoke.sv \
    && /opt/slang/12.0/bin/slang --lint-only smoke.sv

FROM ${BASE_IMAGE} AS runtime
ARG BASE_IMAGE
WORKDIR /opt/rtl-agent
COPY --from=slang-build /opt/slang/12.0 /opt/slang/12.0
COPY agent ./agent
COPY serve ./serve
COPY skill ./skill
COPY model ./model
COPY baseline.py run.sh run_baseline.sh manifest.json MODEL.md ./
# Inference stack is prepared in the approved base image before offline use.
# The pinned slang binary is copied from the verified build stage; no runtime download.
RUN python3 -m serve.prepare_image --base-image "$BASE_IMAGE" && chmod +x run.sh run_baseline.sh serve/*.sh
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENV SLANG_BIN=/opt/slang/12.0/bin/slang
RUN bash serve/check_image.sh
# serve/env.sh supplies public paths; model files are pre-mounted at /models.
CMD ["bash", "serve/entrypoint.sh"]

# Development image: cloud inference over HTTPS, EDA remains inside this Linux image.
# Credentials are supplied at runtime, never through build arguments or image layers.
FROM runtime AS test
COPY tools/serve_development.py ./tools/serve_development.py
CMD ["python3", "tools/serve_development.py"]

# Keep the default build production-only; no cloud bridge is copied into this stage.
FROM runtime AS production
