"""Détection AT-SPI sans lecture du contenu des champs."""
import json
import sys


def main():
    import pyatspi as a
    from gi.repository import GLib
    current = [None, None]
    serial = [0]

    def eligible(source):
        try:
            source.clearCache()
            state = source.getState()
            app = source.getApplication().name.casefold()
            return (app in ('firefox', 'code', 'code - oss', 'codium', 'visual studio code')
                    and state.contains(a.STATE_EDITABLE)
                    and state.contains(a.STATE_FOCUSED)
                    and state.contains(a.STATE_ENABLED)
                    and source.getRole() != a.ROLE_PASSWORD_TEXT)
        except Exception:
            return False

    def publish(source, clicked=False):
        source = source if eligible(source) else None
        if source != current[0]:
            serial[0] += 1
            current[:] = [source, serial[0] if source is not None else None]
            print(json.dumps({'focus': current[1]}), flush=True)
        elif clicked and source is not None:
            print(json.dumps({'focus': current[1], 'clicked': True}), flush=True)

    def scan(clicked=False):
        # Rattrape les événements absents et les champs déjà actifs au démarrage.
        try:
            for app in a.Registry.getDesktop(0):
                if app.name.casefold() not in ('firefox', 'code', 'code - oss', 'codium', 'visual studio code'):
                    continue
                stack = [app]
                visited = 0
                while stack and visited < 2500:
                    node = stack.pop()
                    visited += 1
                    if eligible(node):
                        publish(node, clicked)
                        return False
                    try:
                        stack.extend(child for child in node if child is not None)
                    except Exception:
                        pass
        except Exception:
            pass
        publish(None)
        return False

    def changed(event):
        if event.detail1 and eligible(event.source):
            publish(event.source)
        elif current[0] == event.source and not event.detail1:
            # L’événement peut être ancien : relire l’état actuel du même champ.
            publish(event.source)
        else:
            GLib.timeout_add(60, scan)

    def check():
        if current[0] is not None:
            publish(current[0])
        return True

    def recover():
        if current[0] is None:
            scan()
        return True

    def clicked(event):
        GLib.timeout_add(100, lambda: scan(True))

    a.Registry.registerEventListener(changed, 'object:state-changed:focused')
    a.Registry.registerEventListener(clicked, 'mouse:button:1r')
    GLib.timeout_add(100, check)
    GLib.timeout_add(1000, recover)
    GLib.timeout_add(300, scan)
    print(json.dumps({'ready': True}), flush=True)
    a.Registry.start()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        sys.exit(1)
