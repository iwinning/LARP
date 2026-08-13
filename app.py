from flask import Flask, render_template, request, jsonify
from src.scraper import ScraperConfig, scrape_search_page
import json

app = Flask(__name__)

@app.route('/')
def index():
    """Visa startsidan"""
    return render_template('index.html')

@app.route('/scrape', methods=['POST'])
def scrape():
    """Ta emot data från formuläret och scrapa"""
    try:
        # Hämta data från formuläret
        data = request.get_json()
        url = data.get('url')
        result_selector = data.get('result_selector')
        name_selector = data.get('name_selector')
        address_selector = data.get('address_selector')
        phone_selector = data.get('phone_selector')
        
        # Skapa config
        config = ScraperConfig(
            url=url,
            result_selector=result_selector,
            name_selector=name_selector,
            address_selector=address_selector,
            phone_selector=phone_selector,
            headless=True  # Kör i bakgrunden
        )
        
        # Scrapa
        results = scrape_search_page(config)
        
        # Returnera resultat
        return jsonify({
            'success': True,
            'count': len(results),
            'results': results[:100]  # Max 100 resultat
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
