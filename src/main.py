import os
import sys
# DON'T CHANGE THIS !!!
# This line adds the parent directory of the current script's directory to the Python path.
# This is useful for making modules in sibling directories importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, send_from_directory
from flask_cors import CORS
# from src.models.user import db # Removed
# from src.routes.user import user_bp # Removed
from src.routes.youtube import youtube_bp

# Initialize the Flask app
# The static_folder is set to the 'static' directory relative to this file.
app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# Enable Cross-Origin Resource Sharing (CORS) for all routes
CORS(app)

# Register the blueprint for the YouTube routes
# app.register_blueprint(user_bp, url_prefix='/api/users') # Removed
app.register_blueprint(youtube_bp, url_prefix='/api')

# Uncomment the following lines if you need to use a database
# app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(os.path.dirname(__file__), 'database', 'app.db')}"
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# db.init_app(app)
# with app.app_context():
#     db.create_all()

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    """
    Serves static files from the static folder.
    If a path is provided and the file exists, it serves that file.
    Otherwise, it serves the index.html file as a fallback for client-side routing.
    """
    static_folder_path = app.static_folder
    if static_folder_path is None:
            return "Static folder not configured", 404

    # If the path is not empty and the file exists, send the requested file
    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        # Otherwise, try to send the index.html file
        index_path = os.path.join(static_folder_path, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(static_folder_path, 'index.html')
        else:
            return "index.html not found", 404


# This block runs the app when the script is executed directly
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)