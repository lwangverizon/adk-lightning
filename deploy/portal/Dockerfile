# Build web/data first; build context must be the prepared portal directory only.
# Supply an approved nginx-unprivileged image pinned by digest.
# No implicit private registry, mutable tag or unreviewed platform selection.
ARG PORTAL_BASE
FROM ${PORTAL_BASE}
COPY --chown=101:101 nginx.conf /etc/nginx/nginx.conf
COPY --chown=101:101 web/ /srv/portal/
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
