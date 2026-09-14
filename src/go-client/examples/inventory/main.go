package main

import (
	"context"
	"fmt"
	testbench "gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client"
	"log"
	"net/url"
	"time"
)

func main() {
	client, err := testbench.FromEnv(testbench.Options{Retries: 2})
	if err != nil {
		log.Fatal(err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	err = client.Devices.Iterate(ctx, testbench.ListOptions{Query: url.Values{"status": {"available"}}}, func(device testbench.Record) error {
		fmt.Printf("%s: %v\n", device.String("unique_id"), device.Field("lan_ip"))
		return nil
	})
	if err != nil {
		log.Fatal(err)
	}
}
