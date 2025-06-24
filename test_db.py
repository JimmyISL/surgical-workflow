from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return "Hello World"

@app.route('/test')
def test():
    return jsonify({'test': True})

if __name__ == '__main__':
    print("Starting minimal test app...")
    print("Registered routes:")
    for rule in app.url_map.iter_rules():
        print(f"  {rule.rule} -> {rule.endpoint}")
    
    app.run(debug=True, port=5001)  # Use different port