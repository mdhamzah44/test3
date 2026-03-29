function goToProducts() {
  window.location.href = "products.html";
}
/* DARK MODE BUTTON */
const toggle = document.createElement("div");
toggle.className = "toggle-theme";
toggle.innerText = "🌓 Mode";
document.body.appendChild(toggle);

toggle.onclick = () => {
  document.body.classList.toggle("dark");
};

/* BACK TO TOP BUTTON */
const topBtn = document.createElement("button");
topBtn.id = "topBtn";
topBtn.innerText = "↑ Top";
document.body.appendChild(topBtn);

window.onscroll = () => {
  topBtn.style.display = window.scrollY > 400 ? "block" : "none";
};

topBtn.onclick = () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
};

/* SCROLL REVEAL */
document.querySelectorAll("section, .card").forEach(el => {
  el.classList.add("reveal");
});

window.addEventListener("scroll", () => {
  document.querySelectorAll(".reveal").forEach(el => {
    if (el.getBoundingClientRect().top < window.innerHeight - 100) {
      el.classList.add("active");
    }
  });
});

/* NEWSLETTER POPUP */
setTimeout(() => {
  const popup = document.createElement("div");
  popup.className = "popup";
  popup.innerHTML = `
    <div class="popup-content">
      <h3>Join SportX</h3>
      <p>Get exclusive drops & offers</p>
      <input type="email" placeholder="Email" /><br><br>
      <button onclick="this.closest('.popup').remove()">Close</button>
    </div>
  `;
  document.body.appendChild(popup);
  popup.style.display = "flex";
}, 3000);