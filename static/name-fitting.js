window.createPairedNameFitter = function (names) {
  let baseFontSize = 0;

  function fitsAtSize(fontSize) {
    names.forEach(name => {
      name.style.fontSize = `${fontSize}px`;
    });
    return names.every(name => {
      const textRange = document.createRange();
      textRange.selectNodeContents(name);
      const textWidth = textRange.getBoundingClientRect().width;
      const container = name.parentElement;
      const style = window.getComputedStyle(container);
      const horizontalInsets = [
        style.paddingLeft,
        style.paddingRight,
        style.borderLeftWidth,
        style.borderRightWidth
      ].reduce((total, inset) => total + Number.parseFloat(inset), 0);
      const availableWidth = container.getBoundingClientRect().width - horizontalInsets;
      return textWidth <= availableWidth - 1;
    });
  }

  function fit() {
    if (baseFontSize <= 0 || names.some(name => name.clientWidth <= 0)) return;

    const minimumFontSize = Math.min(baseFontSize, 12);
    if (fitsAtSize(baseFontSize)) return;
    if (!fitsAtSize(minimumFontSize)) return;

    let low = minimumFontSize;
    let high = baseFontSize;
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const middle = (low + high) / 2;
      if (fitsAtSize(middle)) {
        low = middle;
      } else {
        high = middle;
      }
    }
    fitsAtSize(low);
  }

  function scheduleFit() {
    window.requestAnimationFrame(fit);
  }

  if (typeof ResizeObserver === "function") {
    const observedWidths = new WeakMap();
    const observer = new ResizeObserver(entries => {
      let widthChanged = false;
      entries.forEach(entry => {
        const width = entry.contentRect.width;
        const changed = observedWidths.get(entry.target) !== width;
        observedWidths.set(entry.target, width);
        widthChanged = widthChanged || changed;
      });
      if (widthChanged) scheduleFit();
    });
    new Set(names.map(name => name.parentElement)).forEach(parent => observer.observe(parent));
  } else {
    window.addEventListener("resize", scheduleFit);
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(scheduleFit);
  }

  return fontSize => {
    if (!Number.isFinite(fontSize) || fontSize <= 0) {
      throw new TypeError("Paired name font size must be a positive number");
    }
    baseFontSize = fontSize;
    fit();
  };
};
