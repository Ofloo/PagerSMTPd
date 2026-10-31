# Salt (Swarm, ~10 nodes)

Doel: oude/ongebruikte images per node opruimen zonder volumes of draaiende
containers te raken. Prune werkt **lokaal per node**, dus target alle
Swarm-minions (manager + workers), niet alleen de manager.

## 1. Target verifiëren
```sh
salt -C 'G@roles:swarm' test.ping
salt -C 'G@roles:swarm' grains.item os id
```
Gebruik je `os:Debian`? Dan matcht dat ook niet-Swarm Debian-hosts:
```sh
salt -C 'G@os:Debian and G@roles:swarm' test.ping
```

## 2. Kijken wat er weg zou gaan
```sh
salt --batch 2 -C 'G@roles:swarm' cmd.run 'docker image ls -q | wc -l; docker volume ls -q | wc -l'
```

## 3. Prunen (images, ouder dan 24h)
```sh
salt --batch 2 -C 'G@roles:swarm' cmd.run 'docker image prune -af --filter "until=24h"'
```
Of alleen PagerSMPTd-images:
```sh
salt --batch 2 -C 'G@roles:swarm' cmd.run \
  'docker image prune -af --filter "label=org.opencontainers.image.title=PagerSMPTd"'
```

## 4. State (herhaalbaar)
```sh
salt --batch 2 -C 'G@roles:swarm' state.apply pagersmtpd-prune
```
`deploy/salt/pagersmtpd-prune.sls` moet dan in de Salt file_roots staan
(bv. `/srv/salt/pagersmtpd-prune.sls`).

## 4b. Volumes (NFS-backed: veilig)
Op deze Swarm zijn volumes NFS-backed. `docker volume prune -f` verwijdert dan
alleen de ongebruikte volume-definities; de bestanden blijven op de NFS-server
staan. Op nodes met lokale (non-NFS) volumes met data: niet doen.
```sh
salt --batch 2 -C 'G@roles:swarm' cmd.run 'docker volume prune -f'
```
Of in één keer, zoals je gewend bent:
```sh
salt --batch 2 -C 'G@roles:swarm' cmd.run 'docker system prune -a --volumes -f'
```
(let op: dat ruimt ook images van andere stacks op die nodes op — die pullen
vanzelf weer bij de volgende deploy/reschedule).

## 5. Image opnieuw pullen na prune
```sh
salt -C 'G@roles:manager' cmd.run \
  'docker service update --force --image ofloo/pagersmtpd:latest pagersmtp_pager-smtpd'
```

## 6. Verifiëren
```sh
salt -C 'G@roles:swarm' cmd.run 'docker images --filter=reference="ofloo/pagersmtpd" --format "{{.Repository}}:{{.Tag}} {{.ID}}"'
salt -C 'G@roles:manager' cmd.run 'docker service ps pagersmtp_pager-smtpd --no-trunc'
```

## Waarschuwing
`docker system prune -a --volumes -f` verwijdert alle ongebruikte images én
volumes op elke getargete node. Met NFS-backed volumes is het dataverlies nihil
(de NFS-data blijft), maar images van andere stacks worden ook opgeruimd en
moeten opnieuw gepulld worden. Op nodes met lokale volumes: `--volumes` weglaten.
