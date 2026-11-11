"""Minimal rotctld-compatible server so Gpredict and friends can drive the dish.

A tidied version of upstream carryout_rotor.py: it understands "p" (get
position), "P az el" (set position) and "S" (stop), accepts reconnects, and
works in true coordinates using the calibrated offsets.
"""
import socket

from .dish import DishError


def serve(dish, host="127.0.0.1", port=4533, log=print):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    log(f"Listening for rotctld commands on {host}:{port} (Ctrl-C to quit)")
    az, el = 0.0, dish.el_min + dish.el_offset
    try:
        while True:
            conn, addr = server.accept()
            log(f"Connection from {addr[0]}")
            with conn:
                while True:
                    data = conn.recv(100)
                    if not data:
                        break
                    cmd = data.decode("utf-8", errors="replace").split()
                    if not cmd:
                        continue
                    if cmd[0] == "p":
                        conn.sendall(f"{az:.2f}\n{el:.2f}\n".encode())
                    elif cmd[0] == "P" and len(cmd) >= 3:
                        try:
                            new_az, new_el = float(cmd[1]), float(cmd[2])
                            dish.goto(new_az, new_el, wait=False)
                            az, el = new_az, new_el
                            conn.sendall(b"RPRT 0\n")
                        except (ValueError, DishError) as exc:
                            log(f"Rejected {cmd[1:]}: {exc}")
                            conn.sendall(b"RPRT -1\n")
                    elif cmd[0] == "S":
                        conn.sendall(b"RPRT 0\n")
                        break
                    else:
                        conn.sendall(b"RPRT -4\n")
            log("Client disconnected")
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
