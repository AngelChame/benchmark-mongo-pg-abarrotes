// Índices de MongoDB (D-017): dos índices simples, en fecha y en folio.
db = db.getSiblingDB("benchmark");
db.ventas.createIndex({ fecha: 1 }, { name: "fecha_1" });
db.ventas.createIndex({ folio: 1 }, { name: "folio_1" });
printjson(db.ventas.getIndexes().map(i => i.name));
