CREATE TABLE invoices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  invoice_number VARCHAR(255) NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  due_date DATE,
  status VARCHAR(255),
  FOREIGN KEY (company_id) REFERENCES companies(id)
);
