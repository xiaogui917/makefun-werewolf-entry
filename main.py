import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENV_URL = ""
STATEMENT_LIMIT = 500


def post(path, payload, timeout=300):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        ENV_URL + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(5):
        try:
            with opener.open(request, timeout=timeout) as response:
                status = response.getcode()
                body = response.read()
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = {}
            return status, parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as err:
            try:
                body = err.read()
                parsed = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, OSError):
                parsed = {}
            return err.code, parsed if isinstance(parsed, dict) else {}
        except (urllib.error.URLError, OSError):
            if attempt < 4:
                time.sleep(1)
    return 0, {}


def fold(text):
    return re.sub(r"\s+", " ", str(text)).strip()[:STATEMENT_LIMIT]


def analyze(view):
    you = view.get("you", {})
    me = you.get("seat")
    my_role = you.get("role")
    partner_info = you.get("partner")
    partner = partner_info.get("seat") if isinstance(partner_info, dict) else None
    players = {}
    names = {}
    for player in view.get("players", []):
        seat = player.get("seat")
        if seat is not None:
            players[seat] = {"alive": bool(player.get("alive")), "role": player.get("role")}
            names[seat] = player.get("name", "Seat " + str(seat))

    deaths = {}
    speeches = []
    votes = []
    exiles = []
    for entry in view.get("log", []):
        kind = entry.get("kind")
        day = entry.get("day", 0)
        if kind in ("dawn", "exile"):
            seat = entry.get("died") if kind == "dawn" else entry.get("seat")
            role = entry.get("role")
            if seat is not None and role is not None:
                deaths[seat] = role
        if kind == "speech":
            speeches.append({
                "day": day, "seat": entry.get("seat"),
                "round": entry.get("round", 0), "text": str(entry.get("text", "")),
            })
        elif kind == "vote":
            votes.append({"day": day, "seat": entry.get("seat"), "target": entry.get("target")})
        elif kind == "exile":
            exiles.append({"day": day, "seat": entry.get("seat"), "role": entry.get("role"),
                           "tally": entry.get("tally")})
    today_votes = [vote for vote in votes if vote["day"] == view.get("day")]
    today_vote_counts = {}
    for vote in today_votes:
        target = vote["target"]
        if target is not None:
            today_vote_counts[target] = today_vote_counts.get(target, 0) + 1

    claimants = {}
    accusations = {}
    for speech in speeches:
        seat = speech["seat"]
        if seat is None or seat == me:
            continue
        text = speech["text"]
        low = text.lower()
        if any(phrase in low for phrase in ("i am the seer", "i'm the seer", "as the seer", "i am seer")):
            claimants.setdefault(seat, [])
        for match in re.finditer(r"\bseat\s+(\d+)\b", low):
            target = int(match.group(1))
            tail = low[match.end():match.end() + 100]
            before = low[max(0, match.start() - 55):match.start()]
            is_wolf = None
            if re.match(r"\s+(?:is\s+)?(?:not\s+a\s+werewolf|clean|innocent|village)\b", tail):
                is_wolf = False
            elif re.match(r"\s+(?:is\s+)?(?:a\s+)?(?:werewolf|wolf)\b", tail):
                is_wolf = True
            elif "checked" in before and "werewolf" in tail:
                is_wolf = True
            if is_wolf is not None and seat in claimants:
                claimants[seat].append((target, is_wolf))
            context = low[max(0, match.start() - 45):match.end() + 65]
            if re.search(r"werewolf|\bwolf\b|suspicious|lying", context):
                accusations.setdefault(target, 0)
                accusations[target] += 1

    my_wolf_results = set()
    my_clean_results = set()
    if my_role == "seer":
        for result in view.get("seerResults") or []:
            if result.get("werewolf"):
                my_wolf_results.add(result.get("seat"))
            else:
                my_clean_results.add(result.get("seat"))

    living_claimants = set(claimants)
    trusted = set()
    conflicting_claimants = set()
    for claimant, results in claimants.items():
        conflict = False
        seen = {}
        for target, is_wolf in results:
            if target in seen and seen[target] != is_wolf:
                conflict = True
            seen[target] = is_wolf
            revealed = deaths.get(target) or players.get(target, {}).get("role")
            if revealed:
                revealed_wolf = "wolf" in str(revealed).lower()
                if revealed_wolf != is_wolf:
                    conflict = True
        if conflict:
            conflicting_claimants.add(claimant)
    for claimant, results in claimants.items():
        if claimant not in conflicting_claimants and not any(
            claimant != other and any(a != b for a, b in _shared_claim_results(results, claimants[other]))
            for other in claimants
        ):
            trusted.add(claimant)
    if my_role == "seer":
        trusted = set()
    elif len(trusted) != 1:
        trusted = set()

    known_wolf = set()
    known_clean = set()
    for seat, data in players.items():
        role = deaths.get(seat) or data.get("role")
        if role:
            if "wolf" in str(role).lower():
                known_wolf.add(seat)
            else:
                known_clean.add(seat)
    if my_role == "seer":
        known_wolf.update(my_wolf_results)
        known_clean.update(my_clean_results)
        known_wolf.update(claimants)
    for claimant in trusted:
        for target, is_wolf in claimants.get(claimant, []):
            (known_wolf if is_wolf else known_clean).add(target)

    def score_for(seat, private=True):
        score = 0
        if seat in known_wolf:
            score += 100
        if seat in known_clean and (private or seat not in my_clean_results):
            score -= 100
        for vote in votes:
            voter, target = vote["seat"], vote["target"]
            if voter == seat and target in deaths:
                score += -15 if "wolf" in str(deaths[target]).lower() else 12
            if voter == seat and any(
                other["seat"] != seat and other["day"] == vote["day"]
                and other["target"] == target and other["seat"] in known_wolf
                for other in votes
            ):
                score += 6
            if target == seat and voter in trusted:
                score += 10
        if seat in claimants:
            score += 25 if seat in conflicting_claimants or seat not in trusted else 0
        if view.get("day", 0) >= 2 and not any(s["seat"] == seat for s in speeches):
            score += 4
        score += min(6, accusations.get(seat, 0) * 2)
        return score

    living = [seat for seat, data in players.items() if data["alive"]]
    # Public suspicion deliberately excludes private seer results.
    private_known = known_wolf.copy()
    public_wolves = {
        seat for seat, role in deaths.items() if "wolf" in str(role).lower()
    }
    public_clean = {
        seat for seat, role in deaths.items() if "wolf" not in str(role).lower()
    }
    for claimant in trusted:
        for target, is_wolf in claimants.get(claimant, []):
            (public_wolves if is_wolf else public_clean).add(target)
    known_wolf = public_wolves
    private_clean = known_clean
    known_clean = public_clean
    public_scores = {seat: score_for(seat, private=False) for seat in living}
    known_wolf = private_known
    known_clean = private_clean
    scores = {seat: score_for(seat, private=True) for seat in living}
    return {
        "me": me, "my_role": my_role, "partner": partner, "players": players, "names": names,
        "deaths": deaths, "speeches": speeches, "votes": votes, "today_votes": today_votes,
        "today_vote_counts": today_vote_counts,
        "exiles": exiles, "claimants": claimants, "trusted": trusted,
        "conflicting_claimants": conflicting_claimants, "accusations": accusations,
        "my_wolf_results": my_wolf_results, "my_clean_results": my_clean_results,
        "known_wolf": known_wolf, "known_clean": known_clean, "scores": scores,
        "public_score": public_scores,
    }


def _shared_claim_results(first, second):
    lookup = dict(second)
    return [(value, lookup[value]) for value, result in first if value in lookup and result != lookup[value]]


def _options(options):
    result = []
    for item in options or []:
        seat = item.get("seat") if isinstance(item, dict) else item
        if seat is not None and seat not in result:
            result.append(seat)
    return sorted(result)


def decide_kill(view, analysis, options):
    opts = _options(options)
    partner = analysis["partner"]
    for vote in view.get("wolfVotes") or []:
        if vote.get("night") == view.get("day") and vote.get("seat") == partner:
            if vote.get("target") in opts:
                return vote.get("target")
    if not opts:
        return None
    top_public = set(sorted(analysis["public_score"], key=lambda s: (-analysis["public_score"][s], s))[:2])
    def rank(seat):
        score = 0
        if seat in analysis["claimants"] and (seat in analysis["trusted"] or any(
            result is True and target in (analysis["me"], partner)
            for target, result in analysis["claimants"].get(seat, [])
        )):
            score += 50
        elif seat in analysis["claimants"]:
            score += 40
        if analysis["accusations"].get(analysis["me"], 0) and any(
            s["seat"] == seat and re.search(r"seat\s+" + str(analysis["me"]) + r"\b.*(?:werewolf|wolf|suspicious|lying)",
                                            s["text"].lower()) for s in analysis["speeches"]
        ):
            score += 25
        if partner is not None and any(
            s["seat"] == seat and re.search(r"seat\s+" + str(partner) + r"\b.*(?:werewolf|wolf|suspicious|lying)",
                                            s["text"].lower()) for s in analysis["speeches"]
        ):
            score += 25
        score += min(15, sum(1 for s in analysis["speeches"] if s["seat"] == seat) * 3)
        if view.get("day") == 1:
            day_one_counts = {}
            for speech in analysis["speeches"]:
                if speech["day"] == 1:
                    day_one_counts[speech["seat"]] = day_one_counts.get(speech["seat"], 0) + 1
            if day_one_counts and day_one_counts.get(seat, 0) == max(day_one_counts.values()):
                score += 8
        score += 15 * sum(1 for v in analysis["votes"] if v["seat"] == seat and v["target"] in analysis["deaths"]
                          and "wolf" in str(analysis["deaths"][v["target"]]).lower())
        if seat in top_public:
            score -= 20
        return score
    return min(opts, key=lambda seat: (-rank(seat), seat))


def decide_protect(view, analysis, options):
    opts = _options(options)
    if not opts:
        return None
    last = view.get("lastProtect") or {}
    filtered = [seat for seat in opts if seat != last.get("seat")]
    if filtered:
        opts = filtered
    speeches_by_seat = {}
    for speech in analysis["speeches"]:
        speeches_by_seat[speech["seat"]] = speeches_by_seat.get(speech["seat"], 0) + 1
    prior_no_death = any(entry.get("kind") == "dawn" and not entry.get("died")
                         for entry in view.get("log", []))
    def rank(seat):
        score = 0
        if seat in analysis["trusted"]:
            score += 60
        elif seat in analysis["claimants"]:
            score += 40
        if seat != analysis["me"]:
            candidates = [s for s in opts if s != analysis["me"]]
            if candidates and seat == min(candidates, key=lambda s: (analysis["scores"].get(s, 0), -speeches_by_seat.get(s, 0), s)):
                score += 20
        if prior_no_death:
            score += 10
        return score
    return min(opts, key=lambda seat: (-rank(seat), seat))


def decide_inspect(view, analysis, options):
    opts = [seat for seat in _options(options) if seat not in analysis["my_wolf_results"]
            and seat not in analysis["my_clean_results"]
            and not analysis["players"].get(seat, {}).get("role")]
    if not opts:
        return None
    def rank(seat):
        return (30 if seat in analysis["claimants"] else 0) + analysis["scores"].get(seat, 0) + min(
            5, sum(1 for s in analysis["speeches"] if s["seat"] == seat))
    return min(opts, key=lambda seat: (-rank(seat), seat))


def decide_vote(view, analysis, options):
    opts = _options(options)
    role = analysis["my_role"]
    if role not in ("werewolf", "wolf"):
        direct = sorted(seat for seat in opts if seat in analysis["my_wolf_results"])
        if role == "seer" and direct:
            return direct[0]
        if not opts or all(seat in analysis["known_clean"] for seat in opts):
            return None
        eligible = [seat for seat in opts if seat not in analysis["known_clean"]]
        if not eligible:
            eligible = opts
        return min(eligible, key=lambda seat: (
            -(analysis["scores"].get(seat, 0) + 6 * analysis["today_vote_counts"].get(seat, 0)),
            seat,
        ))
    partner = analysis["partner"]
    partner_votes = analysis["today_vote_counts"].get(partner, 0)
    my_votes = analysis["today_vote_counts"].get(analysis["me"], 0)
    if partner in opts and partner_votes >= 3:
        return partner
    eligible = [seat for seat in opts if seat not in (analysis["me"], partner)]
    if my_votes >= 2 and eligible:
        return min(eligible, key=lambda seat: (-analysis["public_score"].get(seat, 0), seat))
    opts = eligible
    for claimant, results in analysis["claimants"].items():
        if claimant in opts and any(target in (analysis["me"], partner) and is_wolf
                                    for target, is_wolf in results):
            return claimant
    if not opts:
        return None
    return min(opts, key=lambda seat: (-analysis["public_score"].get(seat, 0), seat))


def my_speeches(analysis):
    return [speech["text"] for speech in analysis["speeches"] if speech["seat"] == analysis["me"]]


def has_announced(analysis, seat):
    return any(re.search(r"\bseat\s+" + str(seat) + r"\b", text.lower())
               for text in my_speeches(analysis))


def _suspicion_statement(view, analysis):
    me = analysis["me"]
    candidates = [seat for seat, score in analysis["scores"].items()
                  if seat in analysis["players"] and analysis["players"][seat]["alive"]
                  and seat != me and score >= 40]
    if candidates:
        seat = min(candidates, key=lambda s: (-analysis["scores"][s], s))
        reason = "their statements accuse another player without clear support"
        if seat in analysis["claimants"] and seat not in analysis["trusted"]:
            reason = "their seer claim conflicts with the evidence"
        else:
            for vote in analysis["votes"]:
                if vote["seat"] == seat and vote["target"] in analysis["deaths"]:
                    if "wolf" not in str(analysis["deaths"][vote["target"]]).lower():
                        reason = "their vote helped exile a revealed villager"
                        break
        return "I suspect seat " + str(seat) + " because " + reason + "."
    unchecked = sorted(
        seat for seat, data in analysis["players"].items()
        if data["alive"] and seat != me and not data["role"]
        and seat not in analysis["my_wolf_results"] and seat not in analysis["my_clean_results"]
    )
    if unchecked:
        return "I have no firm read yet; seer, please inspect seat " + str(unchecked[0]) + "."
    living = sorted(seat for seat, data in analysis["players"].items() if data["alive"] and seat != me)
    watched = living[:2]
    if len(watched) >= 2:
        return "I have no firm read yet; I am watching seats " + str(watched[0]) + " and " + str(watched[1]) + "."
    return "I have no firm read yet."


def _decide_speak(view, analysis):
    me = analysis["me"]
    role = analysis["my_role"]
    day = view.get("day", 0)
    own = my_speeches(analysis)
    day_own = [speech for speech in analysis["speeches"]
               if speech["seat"] == me and speech["day"] == day]

    if role == "doctor":
        pressure = sum(1 for vote in analysis["today_votes"] if vote["target"] == me)
        if pressure >= 3 and not any("i am the doctor" in text.lower() for text in own):
            protection = view.get("lastProtect")
            if protection:
                return fold("I am the doctor. My latest protection was seat " +
                            str(protection.get("seat")) + ".")
            return "I am the doctor."

    if role == "seer":
        announced_wolves = {seat for seat in analysis["my_wolf_results"] if has_announced(analysis, seat)}
        unannounced_wolf = bool(analysis["my_wolf_results"] - announced_wolves)
        claimed = any("i am the seer" in text.lower() or "i'm the seer" in text.lower()
                      for text in own)
        todays_accusations = 0
        for speech in analysis["speeches"]:
            if speech["day"] != day or speech["seat"] == me:
                continue
            low = speech["text"].lower()
            if re.search(r"\bseat\s+" + str(me) + r"\b", low) and re.search(
                    r"werewolf|\bwolf\b|suspicious|lying|vote", low):
                todays_accusations += 1
        todays_accusations += sum(1 for vote in analysis["today_votes"] if vote["target"] == me)
        pressure_reveal = todays_accusations >= 2
        has_results = bool(analysis["my_wolf_results"] or analysis["my_clean_results"])
        reveal = (unannounced_wolf or
                  (day >= 3 and has_results and not claimed) or
                  pressure_reveal)
        if reveal:
            checks = []
            all_checks = sorted(analysis["my_wolf_results"] | analysis["my_clean_results"])
            for seat in all_checks:
                checks.append("seat " + str(seat) + " is " +
                              ("a werewolf" if seat in analysis["my_wolf_results"] else "not a werewolf"))
            if not checks:
                return fold("I am the seer.")
            wolf_target = min(analysis["my_wolf_results"]) if analysis["my_wolf_results"] else None
            request = " Please vote seat " + str(wolf_target) + "." if wolf_target is not None else ""
            return fold("I am the seer. " + "; ".join(checks) + "." + request)
        known_living_wolves = sorted(
            seat for seat in analysis["my_wolf_results"]
            if analysis["players"].get(seat, {}).get("alive") and seat != me
        )
        if known_living_wolves:
            return fold("I suspect seat " + str(known_living_wolves[0]) +
                        " because their voting pattern has been evasive.")

    if role in ("werewolf", "wolf"):
        counterclaimant = next((
            claimant for claimant in sorted(analysis["trusted"])
            if claimant != me and any(target in (me, analysis["partner"]) and is_wolf
                                      for target, is_wolf in analysis["claimants"].get(claimant, []))
        ), None)
        already_claimed = any("i am the seer" in text.lower() or "i'm the seer" in text.lower()
                              for text in own)
        if counterclaimant is not None and not already_claimed:
            claimant_role = analysis["players"].get(counterclaimant, {}).get("role") or analysis["deaths"].get(counterclaimant)
            clean_candidates = [
                seat for seat, data in analysis["players"].items()
                if data["alive"] and seat not in (me, counterclaimant, analysis["partner"])
                and not data["role"] and seat not in analysis["deaths"]
            ]
            if (not claimant_role or "wolf" in str(claimant_role).lower()) and clean_candidates:
                clean = min(clean_candidates, key=lambda s: (analysis["public_score"].get(s, 0), s))
                return fold("I am the seer. Seat " + str(counterclaimant) +
                            " is a werewolf, and seat " + str(clean) +
                            " is not a werewolf. Seat " + str(counterclaimant) + " is lying.")

        partner = analysis["partner"]
        partner_score = analysis["public_score"].get(partner, -1000)
        defended_today = any(
            "seat " + str(partner) + " sounded consistent to me" in text.lower()
            for text in (speech["text"] for speech in analysis["speeches"]
                         if speech["seat"] == me and speech["day"] == day)
        )
        if (partner is not None and 10 <= partner_score < 100 and not defended_today
                and not any(speech["day"] == day for speech in analysis["speeches"] if speech["seat"] == me)):
            return fold("Seat " + str(partner) + " sounded consistent to me.")
        living = [seat for seat, data in analysis["players"].items() if data["alive"]]
        vote_target = decide_vote(view, analysis, [seat for seat in living if seat != me])
        if vote_target is not None and vote_target != analysis["partner"]:
            return fold("I suspect seat " + str(vote_target) +
                        " because their voting choices have been hard to trust.")

    result = _suspicion_statement(view, analysis)
    round_number = max((speech["round"] for speech in day_own), default=0)
    if round_number >= 2 and not (role == "seer" and analysis["my_wolf_results"]
                                  and any(not has_announced(analysis, seat)
                                          for seat in analysis["my_wolf_results"])):
        return None
    return fold(result) or None


def decide_speak(view, analysis):
    result = _decide_speak(view, analysis)
    if result is None:
        return None
    result = fold(result)
    if not result:
        return None
    if len(result) > STATEMENT_LIMIT:
        result = fold(result[:STATEMENT_LIMIT])
    return result or None


def act(game, decision, result):
    payload = {"game": game, "decision": decision}
    if decision == "speak":
        text_value = result
        if text_value:
            payload["text"] = fold(text_value)
        else:
            payload["pass"] = True
    else:
        payload["target"] = result
    status, _ = post("/act", payload)
    if status == 409:
        return
    if status == 0:
        time.sleep(1)
        post("/act", payload)
        return
    if status not in (200, 201, 202):
        fallback = {"game": game, "decision": decision}
        if decision == "speak":
            fallback["pass"] = True
        else:
            fallback["target"] = None
        post("/act", fallback)


def main():
    global ENV_URL
    ENV_URL = os.environ["MAKEFUN_ENV_URL"].rstrip("/")
    observe_failures = 0
    for _ in range(200000):
        try:
            status, obs = post("/observe", {}, timeout=300)
            if status == 0 or not obs:
                observe_failures += 1
                if observe_failures >= 10:
                    print("stopping after 10 consecutive observe failures", file=sys.stderr)
                    break
                time.sleep(0.5)
                continue
            observe_failures = 0
            if obs.get("done"):
                break
            pending = obs.get("pending") or []
            for item in pending:
                try:
                    decision = item.get("decision")
                    view = item.get("view") or {}
                    analysis = analyze(view)
                    options = item.get("options") or []
                    if decision == "speak":
                        result = decide_speak(view, analysis)
                        if result is not None:
                            result = fold(result)[:min(STATEMENT_LIMIT, item.get("max_chars", STATEMENT_LIMIT))]
                    elif decision == "vote":
                        result = decide_vote(view, analysis, options)
                    elif decision == "kill":
                        result = decide_kill(view, analysis, options)
                    elif decision == "protect":
                        result = decide_protect(view, analysis, options)
                    elif decision == "inspect":
                        result = decide_inspect(view, analysis, options)
                    else:
                        continue
                    act(item.get("game"), decision, result)
                except Exception as err:
                    print("decision error: " + str(err), file=sys.stderr)
            if status == 0 or not pending:
                time.sleep(0.5)
        except Exception as err:
            print("loop error: " + str(err), file=sys.stderr)


if __name__ == "__main__":
    main()
