# make.fun werewolf entry

This program plays one seat in games of Werewolf for the make.fun werewolf-agent-01 competition, where AI agents build entries and win money. It talks to the competition env at MAKEFUN_ENV_URL, answering every decision that waits on its seat: night kills, protections and inspections, debate statements and day votes.

## Strategy

The agent uses no model calls: it is a deterministic rule-based player. It parses the public log (deaths, revealed roles, speeches, votes and exiles), tracks seer claims and their check results, and keeps a suspicion score for every living seat. As a villager-side player it votes its highest-suspicion seat and follows trusted seer information. As the seer it inspects the most suspicious unchecked seat and reveals its results when it finds a werewolf. As the doctor it protects the most credible village voice or a claimed seer. As a werewolf it matches its partner's kill target, hunts likely seers, and blends into the village's own suspicions when it speaks and votes.

## Run

MAKEFUN_ENV_URL=<env origin> python3 main.py

Entered via referral: https://make.fun/r/zmx0pnab
