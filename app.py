"""
Flask web server for the Swedish person-data scraper.
Serves the browser UI and exposes a /scrape endpoint.
"""

import csv
import io
import json
import os

from flask import Flask, jsonify, render_template, request, Response

from scrapa_alla import hamta_personer, KALLOR

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret")


@app.route("/")
def index():
    return render_template("index.html", kallor=KALLOR)


@app.route("/scrape", methods=["POST"])
def scrape():
    """Run scraper and return results as JSON."""
    try:
        data = request.get_json(force=True)

        stad = (data.get("stad") or "").strip()
        kalla_val = str(data.get("kalla") or "").strip()
        max_antal = int(data.get("max_antal") or 100)

        if not stad:
            return jsonify({"success": False, "error": "Du måste ange en stad."}), 400

        if kalla_val not in KALLOR:
            return jsonify({"success": False, "error": f"Ogiltig källa: {kalla_val}"}), 400

        if max_antal <= 0:
            return jsonify({"success": False, "error": "max_antal måste vara större än 0."}), 400

        personer = hamta_personer(stad, kalla_val, max_antal)

        results = [
            {
                "name": p.get("namn", ""),
                "phone": p.get("telefon", ""),
                "address": p.get("adress", ""),
                "city": p.get("stad", ""),
                "source": p.get("kalla", ""),
            }
            for p in personer
        ]

        return jsonify({"success": True, "count": len(results), "results": results})

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/download", methods=["POST"])
def download():
    """Convert POSTed JSON results to a CSV file for download."""
    try:
        data = request.get_json(force=True)
        results = data.get("results", [])

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["name", "phone", "address", "city", "source"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)

        csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM so Excel opens correctly
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=personer.csv"},
        )
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
