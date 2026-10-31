# Prune unused Docker images on Swarm nodes (safe: never removes running images).
# Apply to the Docker/Swarm minions only:
#   salt --batch 2 -C 'G@roles:swarm' state.apply pagersmtpd-prune
#
# Images in use by running tasks are never removed; pruned images are pulled
# again by the next `docker service update`/reschedule.
prune-unused-images:
  cmd.run:
    - name: docker image prune -af --filter "until=24h"
    - onlyif: test -n "$(docker images -q)"

# Optional, OFF by default: volumes are data and cannot be recovered.
# Uncomment only after checking `docker volume ls` on every node.
# prune-unused-volumes:
#   cmd.run:
#     - name: docker volume prune -f
#     - onlyif: test -n "$(docker volume ls -q)"
