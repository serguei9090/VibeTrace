package main

import "storage"

// WebServer coordinates HTTP requests to storage.
type WebServer struct {
	driver string
}

// NewWebServer instantiates a server.
func NewWebServer() *WebServer {
	return &WebServer{driver: "sqlite"}
}

// HandleRequest processes an incoming query.
func (ws *WebServer) HandleRequest(queryKey string) string {
	s := storage.NewDatabase(ws.driver)
	return s.Query(queryKey)
}

// StartServer starts the service entrypoint.
func StartServer() string {
	srv := NewWebServer()
	return srv.HandleRequest("status")
}
