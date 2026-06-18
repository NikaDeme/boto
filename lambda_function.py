/* ─────────────────────────────────────────────────────────────
   app.js  —  VisionLab frontend logic
   ───────────────────────────────────────────────────────────── */

// ── Configuration ──────────────────────────────────────────────
// Replace this with your actual API Gateway invoke URL after deployment
const API_ENDPOINT = "https://YOUR_API_GATEWAY_ID.execute-api.us-east-1.amazonaws.com/prod/analyse";

// ── Available HuggingFace models ────────────────────────────────
const MODELS = [
  {
    id: "google/vit-base-patch16-224",
    name: "ViT-Base",
    task: "image-classification",
    desc: "Vision Transformer by Google. Strong general-purpose classifier.",
  },
  {
    id: "microsoft/resnet-50",
    name: "ResNet-50",
    task: "image-classification",
    desc: "Classic residual network. Fast and well-tested on ImageNet.",
  },
  {
    id: "facebook/convnext-base-224",
    name: "ConvNeXt-Base",
    task: "image-classification",
    desc: "Modern CNN by Meta. Combines conv efficiency with transformer accuracy.",
  },
  {
    id: "nateraw/vit-age-classifier",
    name: "Age Classifier",
    task: "image-classification",
    desc: "Fine-tuned ViT for predicting age ranges from facial images.",
  },
  {
    id: "Falconsai/nsfw_image_detection",
    name: "Content Safety",
    task: "image-classification",
    desc: "Detects whether image content is safe or explicit.",
  },
  {
    id: "apple/mobilevit-small",
    name: "MobileViT-S",
    task: "image-classification",
    desc: "Lightweight mobile-friendly vision transformer by Apple.",
  },
];

// ── State ────────────────────────────────────────────────────────
let selectedFile   = null;
let selectedModel  = null;

// ── DOM refs ─────────────────────────────────────────────────────
const dropZone    = document.getElementById("drop-zone");
const fileInput   = document.getElementById("file-input");
const previewWrap = document.getElementById("preview-wrap");
const previewImg  = document.getElementById("preview-img");
const previewName = document.getElementById("preview-name");
const btnRemove   = document.getElementById("btn-remove");
const modelGrid   = document.getElementById("model-grid");
const btnSubmit   = document.getElementById("btn-submit");
const uploadCard  = document.getElementById("upload-card");
const resultCard  = document.getElementById("result-card");
const resultMeta  = document.getElementById("result-meta");
const resultBars  = document.getElementById("result-bars");
const s3Key       = document.getElementById("s3-key");
const dbId        = document.getElementById("db-id");
const btnReset    = document.getElementById("btn-reset");
const toast       = document.getElementById("toast");

// ── Build model cards ─────────────────────────────────────────────
function buildModelGrid() {
  modelGrid.innerHTML = "";
  MODELS.forEach((m) => {
    const card = document.createElement("div");
    card.className = "model-card";
    card.dataset.id = m.id;
    card.innerHTML = `
      <div class="model-check">✓</div>
      <div class="model-name">${m.name}</div>
      <div class="model-task">${m.task}</div>
      <div class="model-desc">${m.desc}</div>
    `;
    card.addEventListener("click", () => selectModel(m.id, card));
    modelGrid.appendChild(card);
  });
}

function selectModel(id, cardEl) {
  // Deselect all
  document.querySelectorAll(".model-card").forEach((c) => c.classList.remove("selected"));
  // Select clicked
  cardEl.classList.add("selected");
  selectedModel = id;
  updateSubmitState();
}

// ── File handling ─────────────────────────────────────────────────
function handleFile(file) {
  if (!file || !file.type.startsWith("image/")) {
    showToast("Please select a valid image file.");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showToast("File is too large. Maximum size is 10 MB.");
    return;
  }
  selectedFile = file;

  const reader = new FileReader();
  reader.onload = (e) => {
    previewImg.src = e.target.result;
    previewName.textContent = file.name;
    dropZone.hidden     = true;
    previewWrap.hidden  = false;
  };
  reader.readAsDataURL(file);
  updateSubmitState();
}

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

btnRemove.addEventListener("click", () => {
  selectedFile       = null;
  fileInput.value    = "";
  previewImg.src     = "";
  previewWrap.hidden = true;
  dropZone.hidden    = false;
  updateSubmitState();
});

// ── Drag & Drop ───────────────────────────────────────────────────
dropZone.addEventListener("dragenter", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragover",  (e) => { e.preventDefault(); });
dropZone.addEventListener("dragleave", ()  => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

// Allow clicking anywhere in the drop zone
dropZone.addEventListener("click", (e) => {
  if (e.target !== document.querySelector(".btn-browse")) {
    fileInput.click();
  }
});

// ── Submit state ──────────────────────────────────────────────────
function updateSubmitState() {
  btnSubmit.disabled = !(selectedFile && selectedModel);
}

// ── Submit ────────────────────────────────────────────────────────
btnSubmit.addEventListener("click", async () => {
  if (!selectedFile || !selectedModel) return;

  // Loading state
  btnSubmit.disabled = true;
  btnSubmit.classList.add("loading");
  btnSubmit.querySelector(".btn-submit-text").textContent = "Analysing";

  try {
    // Convert image to base64
    const base64 = await fileToBase64(selectedFile);

    const payload = {
      image:      base64,
      filename:   selectedFile.name,
      model_id:   selectedModel,
      content_type: selectedFile.type,
    };

    const response = await fetch(API_ENDPOINT, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.message || `HTTP ${response.status}`);
    }

    const data = await response.json();
    showResults(data);

  } catch (err) {
    showToast(`Error: ${err.message}`);
    btnSubmit.disabled = false;
    btnSubmit.classList.remove("loading");
    btnSubmit.querySelector(".btn-submit-text").textContent = "Analyse Image";
  }
});

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => resolve(reader.result.split(",")[1]); // strip data:...;base64,
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ── Show results ──────────────────────────────────────────────────
function showResults(data) {
  // data expected shape:
  // {
  //   predictions: [{ label: string, score: number }],
  //   s3_key:      string,
  //   db_item_id:  string,
  //   model_id:    string,
  //   filename:    string,
  // }

  const model  = MODELS.find((m) => m.id === data.model_id) || { name: data.model_id };
  const preds  = data.predictions || [];

  // Meta line
  resultMeta.innerHTML = `
    Model: <span style="color:var(--accent)">${model.name}</span> &nbsp;·&nbsp;
    File: <span style="color:var(--text)">${data.filename || selectedFile.name}</span> &nbsp;·&nbsp;
    ${preds.length} prediction${preds.length !== 1 ? "s" : ""}
  `;

  // Bar chart
  resultBars.innerHTML = "";
  preds.slice(0, 8).forEach((p, i) => {
    const pct = Math.round(p.score * 100);
    const row = document.createElement("div");
    row.className = "bar-row";
    row.style.animationDelay = `${i * 60}ms`;
    row.innerHTML = `
      <span class="bar-label" title="${p.label}">${p.label}</span>
      <div class="bar-track"><div class="bar-fill" data-pct="${pct}"></div></div>
      <span class="bar-pct">${pct}%</span>
    `;
    resultBars.appendChild(row);
  });

  // Animate bars after render
  requestAnimationFrame(() => {
    document.querySelectorAll(".bar-fill").forEach((el) => {
      el.style.width = el.dataset.pct + "%";
    });
  });

  // Storage info
  s3Key.textContent = data.s3_key   || "—";
  dbId.textContent  = data.db_item_id || "—";

  // Show result card, hide upload card
  uploadCard.hidden = true;
  resultCard.hidden = false;
  resultCard.scrollIntoView({ behavior: "smooth" });
}

// ── Reset ─────────────────────────────────────────────────────────
btnReset.addEventListener("click", () => {
  selectedFile  = null;
  selectedModel = null;
  fileInput.value = "";
  previewImg.src  = "";
  previewWrap.hidden = true;
  dropZone.hidden    = false;

  document.querySelectorAll(".model-card").forEach((c) => c.classList.remove("selected"));

  btnSubmit.disabled = true;
  btnSubmit.classList.remove("loading");
  btnSubmit.querySelector(".btn-submit-text").textContent = "Analyse Image";

  resultCard.hidden = false;
  uploadCard.hidden = false;
  resultCard.hidden = true;

  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ── Toast ─────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg) {
  toast.textContent = msg;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
}

// ── Init ──────────────────────────────────────────────────────────
buildModelGrid();
