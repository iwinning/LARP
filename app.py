echo 'from flask import Flask, render_template, request, jsonify
from src.scraper import ScraperConfig, scrape_search_page

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    try:
        data = request.get_json()
        config = ScraperConfig(
            url=data.get("url"),
            result_selector=data.get("result_selector"),
            name_selector=data.get("name_selector"),
            address_selector=data.get("address_selector"),
            phone_selector=data.get("phone_selector"),
            headless=True
        )
        results = scrape_search_page(config)
        return jsonify({"success": True, "count": len(results), "results": results[:100]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)' > app.py
