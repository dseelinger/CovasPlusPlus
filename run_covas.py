"""Entry point for the COVAS++ core loop."""
from covas.app import App
from covas.single_instance import ensure_single_instance

if __name__ == "__main__":
    # Refuse a second instance before loading anything — two voice loops would share the mic
    # and speakers and talk over each other. Keep the lock referenced for the process lifetime.
    _instance_lock = ensure_single_instance()
    App().run()
