(() => {
  const rand = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
  const screenX = rand(860, 1640);
  const screenY = rand(280, 860);
  const patch = (proto, key, val) => {
    try {
      Object.defineProperty(proto, key, {
        get() {
          return val;
        },
        configurable: true,
      });
    } catch (e) {}
  };
  patch(MouseEvent.prototype, "screenX", screenX);
  patch(MouseEvent.prototype, "screenY", screenY);
})();
