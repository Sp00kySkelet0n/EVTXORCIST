import json
import subprocess
import logging

logger = logging.getLogger("evtx_uploader")

def run_chainsaw(evtx_dir: str) -> dict:
    """Run Chainsaw hunt against EVTX files and return parsed JSON results."""
    try:
        cmd = [
            "chainsaw", "hunt", evtx_dir,
            "-s", "/opt/sigma/rules/",
            "--mapping", "/opt/chainsaw/mappings/sigma-event-logs-all.yml",
            "-r", "/opt/chainsaw/rules/",
            "--json",
            "--skip-errors"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            logger.warning(f"Chainsaw exited with code {result.returncode}: {result.stderr[:500]}")
        
        output = result.stdout.strip()
        if not output:
            return {"detections": [], "summary": {"total": 0}}
        
        detections = json.loads(output)
        
        # Build summary
        severity_counts = {}
        rule_counts = {}
        for det in detections:
            group = det.get("group", "Unknown")
            level = det.get("level", "unknown")
            name = det.get("name", "Unknown Rule")
            severity_counts[level] = severity_counts.get(level, 0) + 1
            rule_counts[name] = rule_counts.get(name, 0) + 1
        
        # Top detections sorted by count
        top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        
        return {
            "detections": detections,
            "summary": {
                "total": len(detections),
                "by_severity": severity_counts,
                "top_rules": [{"name": n, "count": c} for n, c in top_rules]
            }
        }
    except subprocess.TimeoutExpired:
        logger.error("Chainsaw timed out")
        return {"detections": [], "summary": {"total": 0, "error": "Chainsaw timed out"}}
    except Exception as e:
        logger.error(f"Chainsaw error: {e}")
        return {"detections": [], "summary": {"total": 0, "error": str(e)}}
