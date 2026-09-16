FROM python:3.12-slim-bookworm AS source
WORKDIR /source
COPY . .
RUN python tools/portal_source.py --output /tmp/organization.zip \
    && python -c "from zipfile import ZipFile; ZipFile('/tmp/organization.zip').extractall('/export')"

FROM python:3.12-slim-bookworm AS dependencies
WORKDIR /build
COPY site/requirements.txt .
RUN --mount=type=secret,id=pip_config,target=/etc/pip.conf \
    python -m pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim-bookworm AS validation-tools
COPY tools/install_processing_tools.py /tmp/install_processing_tools.py
RUN python /tmp/install_processing_tools.py

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GALLERY_DB_PATH=/home/data/deployment-portal.db \
    GALLERY_TF_WORK=/home/data/tf-runs
WORKDIR /opt/portal
COPY --from=dependencies /wheels /wheels
COPY site/requirements.txt .
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels \
    && groupadd --gid 10001 portal \
    && useradd --uid 10001 --gid portal --no-create-home --shell /usr/sbin/nologin portal \
    && mkdir -p /home/data \
    && chown -R portal:portal /home/data
COPY --from=source /export/site/ ./
COPY --from=source /export/LICENSE ./LICENSE
COPY --from=validation-tools /usr/local/bin/bicep /usr/local/bin/bicep
COPY --from=validation-tools /usr/local/bin/terraform /usr/local/bin/terraform
COPY --from=validation-tools /usr/local/share/licenses/terraform /usr/local/share/licenses/terraform
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "server/container_start.py"]