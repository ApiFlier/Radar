import os, json, redis
from flask import Flask, render_template, jsonify, Response

app = Flask(__name__)
r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=6379, decode_responses=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/planes')
def planes():
    keys = r.keys('plane:*')
    if not keys:
        return jsonify([])
    pipe = r.pipeline()
    for key in keys:
        pipe.hgetall(key)
    results = pipe.execute()
    planes_data = []
    for key, data in zip(keys, results):
        if data and 'lat' in data and 'lon' in data:
            data['callsign'] = key.replace('plane:', '')
            planes_data.append(data)
    return jsonify(planes_data)

@app.route('/api/trail/<callsign>')
def trail(callsign):
    points = r.lrange(f'trail:{callsign}', 0, -1)
    return jsonify([json.loads(p) for p in points])

@app.route('/api/stream')
def stream():
    def event_stream():
        pubsub = r.pubsub()
        pubsub.subscribe('planes_out')
        for message in pubsub.listen():
            if message['type'] == 'message':
                yield f"data: {message['data']}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
