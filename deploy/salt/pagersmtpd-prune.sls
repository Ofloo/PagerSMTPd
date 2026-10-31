# Prune unused Docker images and volumes on Swarm nodes.
# Apply to the Docker/Swarm minions only:
#   salt --batch 2 -C 'G@roles:swarm' state.apply pagersmtpd-prune
#
# Images in use by running tasks are never removed; pruned images are pulled
# again by the next `docker service update`/reschedule.
#
# Volumes in this estate are NFS-backed: pruning only drops the unused volume
# definitions, the data stays on the NFS server. Do NOT enable the volume step
# on any node that still has local (non-NFS) volumes with real data.
prune-unused-images:
  cmd.run:
    - name: docker image prune -af --filter "until=24h"
    - onlyif: test -n "$(docker images -q)"

prune-unused-volumes:
  cmd.run:
    - name: docker volume prune -f
    - onlyif: test -n "$(docker volume ls -q)"
